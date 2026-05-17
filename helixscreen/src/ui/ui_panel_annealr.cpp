// annealr: HelixScreen integration
//
// Licensed under the GNU General Public License v3.0 (GPL-3.0)
// SPDX-License-Identifier: GPL-3.0-or-later
//
// File: ui_panel_annealr.cpp
// Description: AnnealrPanel implementation. Handles profile list,
//              start/pause/resume/cancel controls, and a temperature
//              chart with planned curve overlay and actual temp trace.

#include "ui_panel_annealr.h"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

// HelixScreen infrastructure
#include "annealr_state.h"
#include "moonraker_api.h"
#include "observer_factory.h"
#include "static_panel_registry.h"
#include "ui_update_queue.h"

namespace helix {

// ── Chart colors ────────────────────────────────────────────────────────

static constexpr lv_color_t COLOR_PLANNED      = LV_COLOR_MAKE(0x66, 0xB2, 0xFF);
static constexpr lv_color_t COLOR_PLANNED_DIM   = LV_COLOR_MAKE(0x66, 0xB2, 0xFF);
static constexpr lv_color_t COLOR_ACTUAL        = LV_COLOR_MAKE(0xFF, 0x73, 0x26);
static constexpr lv_color_t COLOR_GRID          = LV_COLOR_MAKE(0x40, 0x40, 0x47);
static constexpr lv_color_t COLOR_GRID_TEXT     = LV_COLOR_MAKE(0x80, 0x80, 0x8C);
static constexpr lv_color_t COLOR_MARKER        = LV_COLOR_MAKE(0x33, 0xFF, 0x66);
static constexpr lv_color_t COLOR_TARGET_LINE   = LV_COLOR_MAKE(0xFF, 0xFF, 0x4D);

static constexpr lv_opa_t   OPA_PLANNED         = LV_OPA_60;
static constexpr lv_opa_t   OPA_PLANNED_FUTURE  = LV_OPA_30;
static constexpr lv_opa_t   OPA_ACTUAL           = LV_OPA_COVER;
static constexpr lv_opa_t   OPA_MARKER           = LV_OPA_90;
static constexpr lv_opa_t   OPA_TARGET           = LV_OPA_30;

// Chart margins (px)
static constexpr int MARGIN_LEFT   = 45;
static constexpr int MARGIN_RIGHT  = 10;
static constexpr int MARGIN_TOP    = 15;
static constexpr int MARGIN_BOTTOM = 30;

// ── Singleton ───────────────────────────────────────────────────────────

AnnealrPanel& AnnealrPanel::instance() {
    static AnnealrPanel inst;
    return inst;
}

AnnealrPanel::AnnealrPanel()
    : annealr_state_(AnnealrState::instance()) {}

AnnealrPanel::~AnnealrPanel() {
    if (lv_is_initialized()) {
        chart_timer_.reset();
    }
}

// ── Lifecycle ───────────────────────────────────────────────────────────

void AnnealrPanel::init_subjects() {
    if (subjects_initialized_) return;

    spdlog::info("[AnnealrPanel] Initializing panel subjects");

    // Selected profile name (panel-local)
    std::memset(selected_profile_buf_, 0, sizeof(selected_profile_buf_));
    lv_subject_init_string(&selected_profile_, selected_profile_buf_, nullptr,
                           sizeof(selected_profile_buf_), "");

    // Button state subjects
    lv_subject_init_int(&can_start_, 0);
    lv_subject_init_int(&can_pause_, 0);
    lv_subject_init_int(&can_resume_, 0);
    lv_subject_init_int(&can_cancel_, 0);

    // Register globally for XML binding
    lv_xml_register_subject(nullptr, "annealr_selected_profile", &selected_profile_);
    lv_xml_register_subject(nullptr, "annealr_can_start", &can_start_);
    lv_xml_register_subject(nullptr, "annealr_can_pause", &can_pause_);
    lv_xml_register_subject(nullptr, "annealr_can_resume", &can_resume_);
    lv_xml_register_subject(nullptr, "annealr_can_cancel", &can_cancel_);

    subjects_initialized_ = true;

    // Register XML event callbacks
    lv_xml_register_event_cb(nullptr, "on_annealr_start", AnnealrPanel::on_start_clicked);
    lv_xml_register_event_cb(nullptr, "on_annealr_pause", AnnealrPanel::on_pause_clicked);
    lv_xml_register_event_cb(nullptr, "on_annealr_resume", AnnealrPanel::on_resume_clicked);
    lv_xml_register_event_cb(nullptr, "on_annealr_cancel", AnnealrPanel::on_cancel_clicked);

    // Self-register cleanup
    StaticPanelRegistry::instance().register_destroy(
        "AnnealrPanel", []() {
            auto& p = AnnealrPanel::instance();
            if (p.subjects_initialized_) {
                lv_subject_deinit(&p.selected_profile_);
                lv_subject_deinit(&p.can_start_);
                lv_subject_deinit(&p.can_pause_);
                lv_subject_deinit(&p.can_resume_);
                lv_subject_deinit(&p.can_cancel_);
                p.subjects_initialized_ = false;
            }
        });
}

void AnnealrPanel::setup(lv_obj_t* panel, lv_obj_t* /*parent*/) {
    panel_ = panel;

    // Find named widgets from the XML tree
    profile_list_ = lv_obj_find_by_name(panel_, "annealr_profile_list");
    btn_start_    = lv_obj_find_by_name(panel_, "annealr_btn_start");
    btn_pause_    = lv_obj_find_by_name(panel_, "annealr_btn_pause");
    btn_resume_   = lv_obj_find_by_name(panel_, "annealr_btn_resume");
    btn_cancel_   = lv_obj_find_by_name(panel_, "annealr_btn_cancel");
    chart_canvas_ = lv_obj_find_by_name(panel_, "annealr_chart");

    // Set up the chart drawing area
    if (chart_canvas_) {
        lv_obj_add_event_cb(chart_canvas_, AnnealrPanel::chart_draw_cb,
                            LV_EVENT_DRAW_MAIN, this);
    }

    // Get API reference
    api_ = get_moonraker_api();

    // Populate the profile list
    populate_profile_list();

    // Wire up reactive observers
    setup_observers();
}

void AnnealrPanel::on_activate() {
    spdlog::debug("[AnnealrPanel] Activated");

    // Start chart refresh timer
    chart_timer_.reset(lv_timer_create(
        AnnealrPanel::timer_cb, CHART_UPDATE_MS, this));

    // Refresh profile list (may have changed since last visit)
    populate_profile_list();
    update_button_states();
}

void AnnealrPanel::on_deactivate() {
    spdlog::debug("[AnnealrPanel] Deactivated");
    chart_timer_.reset();
}

// ── Observers ───────────────────────────────────────────────────────────

void AnnealrPanel::setup_observers() {
    // Observe annealr state changes to update buttons and redraw chart
    observers_.emplace_back(observe_int_sync<AnnealrPanel>(
        annealr_state_.stage_index_subject(), this,
        [](AnnealrPanel* self, int32_t) {
            self->update_button_states();
            if (self->chart_canvas_) lv_obj_invalidate(self->chart_canvas_);
        }));

    // Observe state string changes for button states
    observers_.emplace_back(observe_string<AnnealrPanel>(
        annealr_state_.state_subject(), this,
        [](AnnealrPanel* self, const char*) {
            self->update_button_states();
            if (self->chart_canvas_) lv_obj_invalidate(self->chart_canvas_);
        }));

    // Observe progress changes to redraw chart
    observers_.emplace_back(observe_int_sync<AnnealrPanel>(
        annealr_state_.progress_subject(), this,
        [](AnnealrPanel* self, int32_t) {
            if (self->chart_canvas_) lv_obj_invalidate(self->chart_canvas_);
        }));

    // Observe profile version changes to rebuild the list
    observers_.emplace_back(observe_int_sync<AnnealrPanel>(
        annealr_state_.profiles_version_subject(), this,
        [](AnnealrPanel* self, int32_t) {
            self->populate_profile_list();
        }));
}

// ── Profile list ────────────────────────────────────────────────────────

void AnnealrPanel::populate_profile_list() {
    if (!profile_list_) return;

    lv_obj_clean(profile_list_);

    const auto& profiles = annealr_state_.profiles();

    for (const auto& profile : profiles) {
        lv_obj_t* btn = lv_list_add_button(profile_list_, nullptr,
                                            profile.name.c_str());
        if (!btn) continue;

        // Store profile index as user data for selection handling
        lv_obj_set_user_data(btn, const_cast<void*>(
            static_cast<const void*>(&profile)));

        // Add description as a sub-label if available
        if (!profile.description.empty()) {
            lv_obj_t* label = lv_obj_get_child(btn, 0);
            if (label) {
                // Build text with description and duration estimate
                float dur = profile.estimate_duration_s();
                char text[256];
                if (dur > 0) {
                    int mins = static_cast<int>(dur / 60.0f);
                    int hours = mins / 60;
                    int rem   = mins % 60;
                    if (hours > 0) {
                        std::snprintf(text, sizeof(text), "%s\n%s (~%dh %dm)",
                                      profile.name.c_str(),
                                      profile.description.c_str(),
                                      hours, rem);
                    } else {
                        std::snprintf(text, sizeof(text), "%s\n%s (~%dm)",
                                      profile.name.c_str(),
                                      profile.description.c_str(), mins);
                    }
                } else {
                    std::snprintf(text, sizeof(text), "%s\n%s",
                                  profile.name.c_str(),
                                  profile.description.c_str());
                }
                lv_label_set_text(label, text);
            }
        }

        // Profile selection handler
        lv_obj_add_event_cb(btn, [](lv_event_t* e) {
            auto* self = static_cast<AnnealrPanel*>(lv_event_get_user_data(e));
            auto* btn  = lv_event_get_target_obj(e);
            auto* prof = static_cast<const AnnealrProfile*>(
                lv_obj_get_user_data(btn));
            if (prof && self) {
                std::strncpy(self->selected_profile_buf_, prof->name.c_str(),
                             sizeof(self->selected_profile_buf_) - 1);
                self->selected_profile_buf_[
                    sizeof(self->selected_profile_buf_) - 1] = '\0';
                lv_subject_copy_string(&self->selected_profile_,
                                       self->selected_profile_buf_);
                self->update_button_states();
                if (self->chart_canvas_) {
                    lv_obj_invalidate(self->chart_canvas_);
                }
            }
        }, LV_EVENT_CLICKED, this);
    }

    if (profiles.empty()) {
        lv_list_add_text(profile_list_, "No profiles found");
    }
}

// ── Button state management ─────────────────────────────────────────────

void AnnealrPanel::update_button_states() {
    bool has_selection = (selected_profile_buf_[0] != '\0');
    bool can_start     = annealr_state_.can_start() && has_selection;
    bool is_running    = annealr_state_.is_run_active()
                         && !annealr_state_.is_paused();
    bool is_paused     = annealr_state_.is_paused();
    bool can_cancel    = annealr_state_.is_run_active();

    lv_subject_set_int(&can_start_,  can_start  ? 1 : 0);
    lv_subject_set_int(&can_pause_,  is_running ? 1 : 0);
    lv_subject_set_int(&can_resume_, is_paused  ? 1 : 0);
    lv_subject_set_int(&can_cancel_, can_cancel ? 1 : 0);
}

// ── GCode commands ──────────────────────────────────────────────────────

void AnnealrPanel::send_gcode(const char* command) {
    if (!api_) {
        spdlog::warn("[AnnealrPanel] Cannot send gcode: API not available");
        return;
    }
    spdlog::info("[AnnealrPanel] Sending: {}", command);
    api_->execute_gcode(command, nullptr, nullptr);
}

void AnnealrPanel::on_start_clicked(lv_event_t* /*e*/) {
    auto& self = AnnealrPanel::instance();
    if (self.selected_profile_buf_[0] == '\0') return;

    char cmd[128];
    std::snprintf(cmd, sizeof(cmd), "ANNEALR_START PROFILE=%s",
                  self.selected_profile_buf_);
    self.clear_temperature_history();
    self.send_gcode(cmd);
}

void AnnealrPanel::on_pause_clicked(lv_event_t* /*e*/) {
    AnnealrPanel::instance().send_gcode("ANNEALR_PAUSE");
}

void AnnealrPanel::on_resume_clicked(lv_event_t* /*e*/) {
    AnnealrPanel::instance().send_gcode("ANNEALR_RESUME");
}

void AnnealrPanel::on_cancel_clicked(lv_event_t* /*e*/) {
    AnnealrPanel::instance().send_gcode("ANNEALR_CANCEL");
}

// ── Temperature recording ───────────────────────────────────────────────

void AnnealrPanel::record_temperature(float temp) {
    float elapsed = static_cast<float>(
        lv_subject_get_int(annealr_state_.run_elapsed_s_subject()));

    temp_history_.push_back({elapsed, temp});

    // Trim old samples
    while (temp_history_.size() > MAX_TEMP_SAMPLES) {
        temp_history_.pop_front();
    }
    float cutoff = elapsed - TEMP_HISTORY_MAX_S;
    while (!temp_history_.empty() && temp_history_.front().elapsed_s < cutoff) {
        temp_history_.pop_front();
    }
}

void AnnealrPanel::clear_temperature_history() {
    temp_history_.clear();
}

// ── Chart timer ─────────────────────────────────────────────────────────

void AnnealrPanel::timer_cb(lv_timer_t* timer) {
    auto* self = static_cast<AnnealrPanel*>(lv_timer_get_user_data(timer));
    if (!self || !self->chart_canvas_) return;

    // Only refresh during active runs
    if (self->annealr_state_.is_run_active()) {
        lv_obj_invalidate(self->chart_canvas_);
    }
}

// ── Chart drawing ───────────────────────────────────────────────────────

void AnnealrPanel::chart_draw_cb(lv_event_t* e) {
    auto* self = static_cast<AnnealrPanel*>(lv_event_get_user_data(e));
    if (self) self->draw_chart(e);
}

void AnnealrPanel::draw_chart(lv_event_t* e) {
    lv_obj_t* obj = lv_event_get_target_obj(e);
    lv_draw_ctx_t* ctx = lv_event_get_draw_ctx(e);

    lv_area_t area;
    lv_obj_get_coords(obj, &area);

    int w = lv_area_get_width(&area);
    int h = lv_area_get_height(&area);

    if (w < 100 || h < 80) return;

    // Chart area inside margins
    lv_area_t chart_area;
    chart_area.x1 = area.x1 + MARGIN_LEFT;
    chart_area.y1 = area.y1 + MARGIN_TOP;
    chart_area.x2 = area.x2 - MARGIN_RIGHT;
    chart_area.y2 = area.y2 - MARGIN_BOTTOM;

    bool is_active = annealr_state_.is_run_active();

    if (is_active) {
        draw_live_chart(ctx, &chart_area);
    } else if (selected_profile_buf_[0] != '\0') {
        draw_profile_preview(ctx, &chart_area);
    } else {
        // "Select a profile" placeholder
        lv_draw_label_dsc_t label_dsc;
        lv_draw_label_dsc_init(&label_dsc);
        label_dsc.color = COLOR_GRID_TEXT;
        label_dsc.font  = lv_font_default();

        lv_point_t size;
        lv_text_get_size(&size, "Select a profile", label_dsc.font,
                         0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);

        lv_area_t text_area;
        text_area.x1 = chart_area.x1 + (lv_area_get_width(&chart_area) - size.x) / 2;
        text_area.y1 = chart_area.y1 + (lv_area_get_height(&chart_area) - size.y) / 2;
        text_area.x2 = text_area.x1 + size.x;
        text_area.y2 = text_area.y1 + size.y;

        lv_draw_label(ctx, &label_dsc, &text_area, "Select a profile", nullptr);
    }
}

void AnnealrPanel::draw_profile_preview(lv_draw_ctx_t* ctx, lv_area_t* area) {
    // Find the selected profile
    const AnnealrProfile* profile = nullptr;
    for (const auto& p : annealr_state_.profiles()) {
        if (p.name == selected_profile_buf_) {
            profile = &p;
            break;
        }
    }
    if (!profile || profile->segments.empty()) return;

    auto points = profile->build_planned_curve(22.0f);
    if (points.empty()) return;

    // Compute axis ranges
    float t_min = 1e9f, t_max = -1e9f;
    for (const auto& [t, temp] : points) {
        t_min = std::min(t_min, temp);
        t_max = std::max(t_max, temp);
    }
    t_min -= 10.0f;
    t_max += 10.0f;
    t_min = std::max(0.0f, t_min);

    float time_max = points.back().first;
    if (time_max <= 0) time_max = 60.0f;

    draw_grid(ctx, area, 0, time_max, t_min, t_max);
    draw_polyline(ctx, area, points, time_max, t_min, t_max,
                  COLOR_PLANNED, OPA_PLANNED, 3);
}

void AnnealrPanel::draw_live_chart(lv_draw_ctx_t* ctx, lv_area_t* area) {
    // Get the active profile for planned curve
    const char* prof_name = annealr_state_.current_state_str();
    // Actually we need the profile name, not state
    const char* active_name = lv_subject_get_string(
        annealr_state_.profile_name_subject());

    const AnnealrProfile* profile = nullptr;
    if (active_name && active_name[0] != '\0') {
        for (const auto& p : annealr_state_.profiles()) {
            if (p.name == active_name) {
                profile = &p;
                break;
            }
        }
    }

    float run_elapsed = static_cast<float>(
        lv_subject_get_int(annealr_state_.run_elapsed_s_subject()));

    // Build planned curve
    std::vector<std::pair<float, float>> planned;
    if (profile) {
        planned = profile->build_planned_curve(22.0f);
    }

    // Compute axis ranges from both planned and actual data
    float t_min = 1e9f, t_max = -1e9f;
    for (const auto& [t, temp] : planned) {
        t_min = std::min(t_min, temp);
        t_max = std::max(t_max, temp);
    }
    for (const auto& sample : temp_history_) {
        t_min = std::min(t_min, sample.temp_c);
        t_max = std::max(t_max, sample.temp_c);
    }

    if (t_min > t_max) { t_min = 12.0f; t_max = 32.0f; }  // fallback
    t_min -= 10.0f;
    t_max += 10.0f;
    t_min = std::max(0.0f, t_min);

    float time_max = run_elapsed;
    if (!planned.empty()) {
        time_max = std::max(time_max, planned.back().first);
    }
    time_max = std::max(time_max, 120.0f);
    time_max *= 1.05f;

    draw_grid(ctx, area, 0, time_max, t_min, t_max);

    // Planned curve
    if (planned.size() >= 2) {
        draw_polyline(ctx, area, planned, time_max, t_min, t_max,
                      COLOR_PLANNED, OPA_PLANNED, 2);
    }

    // Actual temperature trace
    if (!temp_history_.empty()) {
        std::vector<std::pair<float, float>> actual_pts;
        actual_pts.reserve(temp_history_.size());
        for (const auto& s : temp_history_) {
            actual_pts.emplace_back(s.elapsed_s, s.temp_c);
        }
        draw_polyline(ctx, area, actual_pts, time_max, t_min, t_max,
                      COLOR_ACTUAL, OPA_ACTUAL, 3);
    }

    // Current position marker (vertical dashed line)
    if (run_elapsed > 0) {
        int cw = lv_area_get_width(area);
        int ch = lv_area_get_height(area);
        int px = area->x1 + static_cast<int>((run_elapsed / time_max) * cw);

        lv_draw_line_dsc_t line_dsc;
        lv_draw_line_dsc_init(&line_dsc);
        line_dsc.color = COLOR_MARKER;
        line_dsc.opa   = OPA_MARKER;
        line_dsc.width = 1;
        line_dsc.dash_width = 4;
        line_dsc.dash_gap   = 4;

        lv_point_precise_t p1 = {(lv_value_precise_t)px,
                                  (lv_value_precise_t)area->y1};
        lv_point_precise_t p2 = {(lv_value_precise_t)px,
                                  (lv_value_precise_t)area->y2};
        lv_draw_line(ctx, &line_dsc, &p1, &p2);
    }

    // Current target horizontal line
    int stage_target_centi = lv_subject_get_int(
        annealr_state_.stage_target_subject());
    if (stage_target_centi > 0) {
        float target = stage_target_centi / 10.0f;
        int ch = lv_area_get_height(area);
        float temp_range = t_max - t_min;
        if (temp_range > 0) {
            int py = area->y2 - static_cast<int>(
                ((target - t_min) / temp_range) * ch);

            lv_draw_line_dsc_t line_dsc;
            lv_draw_line_dsc_init(&line_dsc);
            line_dsc.color = COLOR_TARGET_LINE;
            line_dsc.opa   = OPA_TARGET;
            line_dsc.width = 1;
            line_dsc.dash_width = 6;
            line_dsc.dash_gap   = 4;

            lv_point_precise_t p1 = {(lv_value_precise_t)area->x1,
                                      (lv_value_precise_t)py};
            lv_point_precise_t p2 = {(lv_value_precise_t)area->x2,
                                      (lv_value_precise_t)py};
            lv_draw_line(ctx, &line_dsc, &p1, &p2);
        }
    }
}

// ── Grid drawing ────────────────────────────────────────────────────────

void AnnealrPanel::draw_grid(lv_draw_ctx_t* ctx, lv_area_t* area,
                              float time_min, float time_max,
                              float temp_min, float temp_max) {
    int cw = lv_area_get_width(area);
    int ch = lv_area_get_height(area);
    float temp_range = temp_max - temp_min;
    float time_range = time_max - time_min;

    if (temp_range <= 0 || time_range <= 0) return;

    lv_draw_line_dsc_t line_dsc;
    lv_draw_line_dsc_init(&line_dsc);
    line_dsc.color = COLOR_GRID;
    line_dsc.opa   = LV_OPA_COVER;
    line_dsc.width = 1;

    lv_draw_label_dsc_t label_dsc;
    lv_draw_label_dsc_init(&label_dsc);
    label_dsc.color = COLOR_GRID_TEXT;
    label_dsc.font  = lv_font_default();  // will use theme font

    // Temperature grid lines (horizontal)
    float temp_step = nice_step(temp_range, 5);
    float t_tick = std::ceil(temp_min / temp_step) * temp_step;
    while (t_tick <= temp_max) {
        int py = area->y2 - static_cast<int>(
            ((t_tick - temp_min) / temp_range) * ch);

        lv_point_precise_t p1 = {(lv_value_precise_t)area->x1,
                                  (lv_value_precise_t)py};
        lv_point_precise_t p2 = {(lv_value_precise_t)area->x2,
                                  (lv_value_precise_t)py};
        lv_draw_line(ctx, &line_dsc, &p1, &p2);

        // Label
        char buf[16];
        std::snprintf(buf, sizeof(buf), "%d\u00b0C", static_cast<int>(t_tick));
        lv_point_t size;
        lv_text_get_size(&size, buf, label_dsc.font, 0, 0,
                         LV_COORD_MAX, LV_TEXT_FLAG_NONE);

        lv_area_t label_area;
        label_area.x1 = area->x1 - size.x - 5;
        label_area.y1 = py - size.y / 2;
        label_area.x2 = label_area.x1 + size.x;
        label_area.y2 = label_area.y1 + size.y;
        lv_draw_label(ctx, &label_dsc, &label_area, buf, nullptr);

        t_tick += temp_step;
    }

    // Time grid lines (vertical)
    float time_step = nice_time_step(time_range, 6);
    float time_tick = std::ceil(time_min / time_step) * time_step;
    while (time_tick <= time_max) {
        int px = area->x1 + static_cast<int>(
            ((time_tick - time_min) / time_range) * cw);

        lv_point_precise_t p1 = {(lv_value_precise_t)px,
                                  (lv_value_precise_t)area->y1};
        lv_point_precise_t p2 = {(lv_value_precise_t)px,
                                  (lv_value_precise_t)area->y2};
        lv_draw_line(ctx, &line_dsc, &p1, &p2);

        // Label
        int mins = static_cast<int>(time_tick / 60.0f);
        char buf[16];
        if (mins >= 60) {
            std::snprintf(buf, sizeof(buf), "%dh%dm", mins / 60, mins % 60);
        } else {
            std::snprintf(buf, sizeof(buf), "%dm", mins);
        }
        lv_point_t size;
        lv_text_get_size(&size, buf, label_dsc.font, 0, 0,
                         LV_COORD_MAX, LV_TEXT_FLAG_NONE);

        lv_area_t label_area;
        label_area.x1 = px - size.x / 2;
        label_area.y1 = area->y2 + 5;
        label_area.x2 = label_area.x1 + size.x;
        label_area.y2 = label_area.y1 + size.y;
        lv_draw_label(ctx, &label_dsc, &label_area, buf, nullptr);

        time_tick += time_step;
    }

    // Chart border
    lv_draw_rect_dsc_t border_dsc;
    lv_draw_rect_dsc_init(&border_dsc);
    border_dsc.bg_opa       = LV_OPA_TRANSP;
    border_dsc.border_color = COLOR_GRID;
    border_dsc.border_width = 1;
    border_dsc.border_opa   = LV_OPA_COVER;
    lv_draw_rect(ctx, &border_dsc, area);
}

// ── Polyline drawing ────────────────────────────────────────────────────

void AnnealrPanel::draw_polyline(
        lv_draw_ctx_t* ctx, lv_area_t* area,
        const std::vector<std::pair<float, float>>& points,
        float time_max, float temp_min, float temp_max,
        lv_color_t color, lv_opa_t opa, int width) {

    if (points.size() < 2) return;

    int cw = lv_area_get_width(area);
    int ch = lv_area_get_height(area);
    float temp_range = temp_max - temp_min;
    if (temp_range <= 0 || time_max <= 0) return;

    lv_draw_line_dsc_t line_dsc;
    lv_draw_line_dsc_init(&line_dsc);
    line_dsc.color = color;
    line_dsc.opa   = opa;
    line_dsc.width = width;
    line_dsc.round_start = true;
    line_dsc.round_end   = true;

    for (size_t i = 1; i < points.size(); ++i) {
        auto [t0, temp0] = points[i - 1];
        auto [t1, temp1] = points[i];

        int px0 = area->x1 + static_cast<int>((t0 / time_max) * cw);
        int py0 = area->y2 - static_cast<int>(
            ((temp0 - temp_min) / temp_range) * ch);
        int px1 = area->x1 + static_cast<int>((t1 / time_max) * cw);
        int py1 = area->y2 - static_cast<int>(
            ((temp1 - temp_min) / temp_range) * ch);

        lv_point_precise_t p1 = {(lv_value_precise_t)px0,
                                  (lv_value_precise_t)py0};
        lv_point_precise_t p2 = {(lv_value_precise_t)px1,
                                  (lv_value_precise_t)py1};
        lv_draw_line(ctx, &line_dsc, &p1, &p2);
    }
}

// ── Axis tick helpers ───────────────────────────────────────────────────

float AnnealrPanel::nice_step(float range, int target_ticks) {
    float raw = range / std::max(target_ticks, 1);
    float magnitude = std::pow(10.0f, std::floor(std::log10(
        std::max(raw, 0.001f))));
    float residual = raw / magnitude;
    if (residual <= 1.5f)      return magnitude;
    else if (residual <= 3.5f) return 2.0f * magnitude;
    else if (residual <= 7.5f) return 5.0f * magnitude;
    return 10.0f * magnitude;
}

float AnnealrPanel::nice_time_step(float range_s, int target_ticks) {
    float raw = range_s / std::max(target_ticks, 1);
    static const float candidates[] = {
        30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200
    };
    for (float c : candidates) {
        if (c >= raw * 0.7f) return c;
    }
    return 7200.0f;
}

}  // namespace helix
