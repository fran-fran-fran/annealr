// annealr: HelixScreen integration
//
// Licensed under the GNU General Public License v3.0 (GPL-3.0)
// SPDX-License-Identifier: GPL-3.0-or-later
//
// File: ui_panel_annealr.h
// Description: HelixScreen panel for controlling annealr annealing/drying
//              runs. Provides profile selection, start/pause/resume/cancel
//              controls, and a temperature chart with planned vs actual traces.

#pragma once

#include "panel_base.h"
#include "annealr_state.h"
#include "observer_factory.h"
#include "ui_timer_guard.h"

#include <lvgl.h>

#include <deque>
#include <string>
#include <utility>
#include <vector>

namespace helix {

class MoonrakerAPI;

class AnnealrPanel : public PanelBase {
public:
    static AnnealrPanel& instance();

    // PanelBase lifecycle
    void init_subjects() override;
    void setup(lv_obj_t* panel, lv_obj_t* parent) override;
    void on_activate() override;
    void on_deactivate() override;

    ~AnnealrPanel() override;

    // XML event callbacks (registered globally)
    static void on_start_clicked(lv_event_t* e);
    static void on_pause_clicked(lv_event_t* e);
    static void on_resume_clicked(lv_event_t* e);
    static void on_cancel_clicked(lv_event_t* e);

private:
    AnnealrPanel();

    AnnealrPanel(const AnnealrPanel&) = delete;
    AnnealrPanel& operator=(const AnnealrPanel&) = delete;

    void setup_observers();
    void populate_profile_list();
    void update_button_states();
    void send_gcode(const char* command);

    // Temperature chart
    void setup_chart();
    void update_chart();
    void record_temperature(float temp);
    void clear_temperature_history();

    // Chart draw callback
    static void chart_draw_cb(lv_event_t* e);
    void draw_chart(lv_event_t* e);
    void draw_profile_preview(lv_draw_ctx_t* ctx, lv_area_t* area);
    void draw_live_chart(lv_draw_ctx_t* ctx, lv_area_t* area);
    void draw_grid(lv_draw_ctx_t* ctx, lv_area_t* chart_area,
                   float time_min, float time_max,
                   float temp_min, float temp_max);
    void draw_polyline(lv_draw_ctx_t* ctx, lv_area_t* chart_area,
                       const std::vector<std::pair<float, float>>& points,
                       float time_max, float temp_min, float temp_max,
                       lv_color_t color, lv_opa_t opa, int width);

    static float nice_step(float range, int target_ticks);
    static float nice_time_step(float range_s, int target_ticks);

    // Timer callback for periodic chart refresh
    static void timer_cb(lv_timer_t* timer);

    // References
    MoonrakerAPI* api_ = nullptr;
    AnnealrState& annealr_state_;

    // Panel LVGL objects
    lv_obj_t* panel_            = nullptr;
    lv_obj_t* profile_list_     = nullptr;
    lv_obj_t* btn_start_        = nullptr;
    lv_obj_t* btn_pause_        = nullptr;
    lv_obj_t* btn_resume_       = nullptr;
    lv_obj_t* btn_cancel_       = nullptr;
    lv_obj_t* chart_canvas_     = nullptr;

    // Observers
    std::vector<ObserverGuard> observers_;
    helix::ui::LvglTimerGuard chart_timer_;

    // Panel-local subjects
    lv_subject_t selected_profile_{};
    char selected_profile_buf_[64] = "";

    /// Button enable/disable subjects (int: 0=disabled, 1=enabled)
    lv_subject_t can_start_{};
    lv_subject_t can_pause_{};
    lv_subject_t can_resume_{};
    lv_subject_t can_cancel_{};

    // Temperature history for live chart
    struct TempSample {
        float elapsed_s;  // seconds since run start
        float temp_c;
    };
    std::deque<TempSample> temp_history_;
    static constexpr size_t MAX_TEMP_SAMPLES = 2000;
    static constexpr float  TEMP_HISTORY_MAX_S = 7200.0f;  // 2 hours

    // Chart update interval
    static constexpr uint32_t CHART_UPDATE_MS = 2000;

    bool subjects_initialized_ = false;
};

}  // namespace helix
