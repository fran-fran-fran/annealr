# Annealr HelixScreen Panel — Integration Guide

This document explains how to integrate the annealr panel into HelixScreen.

## Overview

The annealr HelixScreen integration consists of four files:

| File | Purpose |
|------|---------|
| `include/annealr_state.h` | Domain state class (LVGL subjects for annealr status) |
| `src/printer/annealr_state.cpp` | Status parsing, profile loading, subject management |
| `include/ui_panel_annealr.h` | Panel class (PanelBase subclass) |
| `src/ui/ui_panel_annealr.cpp` | Panel UI logic, chart rendering, GCode dispatch |
| `ui_xml/annealr_panel.xml` | Declarative XML layout |

## Architecture

The panel follows all HelixScreen architectural patterns:

- **Reactive-first**: All UI state is driven through LVGL subjects. Buttons
  are enabled/disabled via `bind_flag_if_eq` bindings in XML. Status text
  updates via `bind_text`. Zero direct widget manipulation after init.

- **Thread-safe**: `AnnealrState::update_from_status()` parses JSON on the
  WebSocket thread and defers all subject writes to the LVGL main thread
  via `helix::ui::queue_update()`.

- **Two-phase init**: `init_subjects()` creates subjects and registers
  cleanup. `setup()` wires observers after XML creation.

- **Self-registering cleanup**: Both `AnnealrState` and `AnnealrPanel`
  register with `StaticSubjectRegistry` / `StaticPanelRegistry` for
  proper shutdown ordering.

- **RAII throughout**: `ObserverGuard` for observers, `LvglTimerGuard`
  for the chart refresh timer.

## Integration Steps

### 1. Copy files into HelixScreen source tree

```bash
cp include/annealr_state.h    <helixscreen>/include/
cp include/ui_panel_annealr.h <helixscreen>/include/
cp src/printer/annealr_state.cpp <helixscreen>/src/printer/
cp src/ui/ui_panel_annealr.cpp   <helixscreen>/src/ui/
cp ui_xml/annealr_panel.xml      <helixscreen>/ui_xml/
```

### 2. Add to build system

In the HelixScreen `CMakeLists.txt` (or `Makefile`), add the two `.cpp`
files to the source list:

```cmake
# In the main executable sources
src/printer/annealr_state.cpp
src/ui/ui_panel_annealr.cpp
```

### 3. Register the XML component

In `subject_initializer.cpp` (or wherever XML components are registered
at startup), add:

```cpp
lv_xml_component_register_from_file("A:/ui_xml/annealr_panel.xml");
```

### 4. Initialize subjects

In `SubjectInitializer::init_core_and_state()` (or equivalent), add:

```cpp
#include "annealr_state.h"

// After other state init_subjects() calls:
AnnealrState::instance().init_subjects();
```

### 5. Initialize the panel

In the panel initialization section (after XML component registration
and subject initialization), add:

```cpp
#include "ui_panel_annealr.h"

// After other panel init_subjects() calls:
AnnealrPanel::instance().init_subjects();
```

### 6. Register for Moonraker status updates

In `moonraker_manager.cpp` (or wherever printer object subscriptions
are configured), subscribe to the `annealr` object:

```cpp
// During printer discovery, if "annealr" is in printer.objects.list:
if (has_annealr) {
    AnnealrState::instance().set_available(true);

    // Subscribe to annealr status updates
    subscription_objects["annealr"] = {
        "state", "profile", "stage_index", "stage_count",
        "stage", "progress", "elapsed_s", "remaining_s",
        "run_elapsed_s"
    };
}
```

Route incoming status to the state class:

```cpp
// In the notify_status_update handler:
if (data.contains("annealr")) {
    AnnealrState::instance().update_from_status(data["annealr"]);
}
```

### 7. Load profiles from config

During printer discovery, when `configfile.config` data is available:

```cpp
// In the discovery callback that processes configfile.config:
AnnealrState::instance().load_profiles_from_config(config_data);
```

### 8. Register navigation

Add a panel ID and navigation entry:

```cpp
// In panel_id.h (or equivalent enum):
enum class PanelId {
    // ... existing panels ...
    Annealr,
};

// In NavigationManager setup, add a nav button or menu entry:
// Conditionally shown when AnnealrState::instance().is_available()
register_panel(PanelId::Annealr, "annealr_panel",
               "Annealing", "thermometer");
```

Wire up panel creation in the navigation handler:

```cpp
case PanelId::Annealr: {
    auto& panel = AnnealrPanel::instance();
    lv_obj_t* obj = lv_xml_create(content_area, "annealr_panel", NULL);
    panel.setup(obj, content_area);
    break;
}
```

### 9. Subscribe to heater temperature for chart

The panel needs chamber temperature readings for the live chart.
In the existing temperature status handler (which already processes
`heater_generic` updates), add a call to record temperature:

```cpp
// When processing heater_generic status for the annealr heater:
if (AnnealrState::instance().is_run_active()) {
    float temp = data["temperature"].get<float>();
    AnnealrPanel::instance().record_temperature(temp);
}
```

## Subject Reference

### Global subjects registered by AnnealrState

| Subject Name | Type | Description |
|---|---|---|
| `annealr_state` | string | "idle", "ramping", "soaking", etc. |
| `annealr_profile_name` | string | Active profile name |
| `annealr_stage_label` | string | E.g. "Ramp to 85°C" |
| `annealr_stage_index` | int | Current segment index |
| `annealr_stage_count` | int | Total segment count |
| `annealr_stage_target` | int | Target temp (centidegrees) |
| `annealr_progress` | int | 0-100 |
| `annealr_elapsed_s` | int | Segment elapsed seconds |
| `annealr_remaining_s` | int | Segment remaining seconds |
| `annealr_run_elapsed_s` | int | Total run elapsed seconds |
| `annealr_status_text` | string | Multi-line status summary |
| `annealr_profiles_version` | int | Bumped on profile list change |

### Panel-local subjects registered by AnnealrPanel

| Subject Name | Type | Description |
|---|---|---|
| `annealr_selected_profile` | string | Currently selected profile name |
| `annealr_can_start` | int | 1 if Start button should be enabled |
| `annealr_can_pause` | int | 1 if Pause button should be enabled |
| `annealr_can_resume` | int | 1 if Resume button should be enabled |
| `annealr_can_cancel` | int | 1 if Cancel button should be enabled |

### XML event callbacks

| Callback Name | Trigger |
|---|---|
| `on_annealr_start` | Start button clicked |
| `on_annealr_pause` | Pause button clicked |
| `on_annealr_resume` | Resume button clicked |
| `on_annealr_cancel` | Cancel button clicked |

## Klipper Plugin Status Dict

The annealr Klipper plugin's `get_status()` returns:

```python
{
    'state': 'idle',           # idle/ramping/soaking/cooling/paused/complete/cancelled/error
    'profile': 'pet_cf',       # profile name or None
    'stage_index': 0,          # current segment index
    'stage_count': 3,          # total segments
    'stage': {                 # None when idle
        'index': 0,
        'label': 'Ramp to 85°C',
        'target': 85.0,
        'kind': 'ramp',
        'elapsed_s': 120.5,
        'remaining_s': 180.0,
        'progress': 0.4,
    },
    'elapsed_s': 0.0,
    'remaining_s': 0.0,
    'progress': 0.0,
    'run_elapsed_s': 300.5,    # total wall time minus pauses
}
```

## Testing

### Mock mode

With `--test`, the panel should work with a `MockAnnealrState` that
simulates profile discovery and state transitions. This can be added
to the existing mock infrastructure following the `MockPrinterState`
pattern.

### SDL2 simulator

The panel is fully functional in the SDL2 desktop simulator. Profile
discovery will show empty until connected to a real or mock Moonraker
instance.

## Dependencies

- LVGL 9.x with XML support enabled (`LV_USE_XML 1`)
- nlohmann/json (already a HelixScreen dependency)
- spdlog (already a HelixScreen dependency)
- HelixScreen core headers: `panel_base.h`, `observer_factory.h`,
  `ui_timer_guard.h`, `ui_update_queue.h`, `static_subject_registry.h`,
  `static_panel_registry.h`, `moonraker_api.h`
