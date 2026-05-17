// annealr: HelixScreen integration
//
// Licensed under the GNU General Public License v3.0 (GPL-3.0)
// SPDX-License-Identifier: GPL-3.0-or-later
//
// File: annealr_state.h
// Description: Domain state class for the annealr Klipper plugin.
//              Owns LVGL subjects for run state, profile info,
//              stage progress, and timing. Updated from Moonraker
//              WebSocket status notifications via queue_update().

#pragma once

#include <lvgl.h>
#include <nlohmann/json_fwd.hpp>

#include <mutex>
#include <string>
#include <vector>

namespace helix {

/// Parsed segment from an annealr_profile config section.
struct AnnealrSegment {
    std::string kind;    // "ramp" or "soak"
    float       target;  // target temperature (°C)
    float       rate;    // ramp rate (°C/min), 0 if unset
    float       duration_s; // explicit duration (seconds), 0 if unset
};

/// Parsed profile from [annealr_profile <name>] config.
struct AnnealrProfile {
    std::string name;
    std::string description;
    std::vector<AnnealrSegment> segments;

    /// Estimate total duration in seconds from a starting temperature.
    float estimate_duration_s(float start_temp = 22.0f) const;

    /// Build (time_s, temp_c) polyline for the planned temperature curve.
    std::vector<std::pair<float, float>> build_planned_curve(
        float start_temp = 22.0f) const;
};

/// Singleton domain state for the annealr plugin.
///
/// Follows the HelixScreen domain decomposition pattern:
/// - init_subjects()  creates LVGL subjects, self-registers cleanup
/// - deinit_subjects() tears them down before lv_deinit()
/// - update_from_status() is called from WebSocket thread;
///   it parses the JSON and defers subject writes via queue_update().
///
/// Subjects are registered globally so XML can bind to them.
class AnnealrState {
public:
    static AnnealrState& instance();

    // Lifecycle
    void init_subjects();
    void deinit_subjects();

    /// Parse profiles from configfile.config discovery data.
    /// Called on main thread during printer discovery.
    void load_profiles_from_config(const nlohmann::json& config);

    /// Process a Moonraker notify_status_update for the "annealr" object.
    /// Called from WebSocket thread — defers to LVGL thread internally.
    void update_from_status(const nlohmann::json& data);

    /// Check whether the annealr plugin was discovered on the printer.
    bool is_available() const { return available_; }
    void set_available(bool available) { available_ = available; }

    // --- Subject accessors (for observer binding in panels) ---

    lv_subject_t* state_subject()          { return &state_; }
    lv_subject_t* profile_name_subject()   { return &profile_name_; }
    lv_subject_t* stage_label_subject()    { return &stage_label_; }
    lv_subject_t* stage_index_subject()    { return &stage_index_; }
    lv_subject_t* stage_count_subject()    { return &stage_count_; }
    lv_subject_t* stage_target_subject()   { return &stage_target_; }
    lv_subject_t* progress_subject()       { return &progress_; }
    lv_subject_t* elapsed_s_subject()      { return &elapsed_s_; }
    lv_subject_t* remaining_s_subject()    { return &remaining_s_; }
    lv_subject_t* run_elapsed_s_subject()  { return &run_elapsed_s_; }
    lv_subject_t* status_text_subject()    { return &status_text_; }

    // --- Profile access ---

    const std::vector<AnnealrProfile>& profiles() const { return profiles_; }

    /// Version counter bumped when profile list changes.
    lv_subject_t* profiles_version_subject() { return &profiles_version_; }

    // --- Convenience queries ---

    /// Current state string (idle, ramping, soaking, cooling, paused, etc.)
    const char* current_state_str() const { return state_buf_; }

    /// True if a run is active (ramping/soaking/cooling/paused).
    bool is_run_active() const;

    /// True if state is exactly "paused".
    bool is_paused() const;

    /// True if state allows starting a new run.
    bool can_start() const;

private:
    AnnealrState() = default;
    ~AnnealrState() = default;

    AnnealrState(const AnnealrState&) = delete;
    AnnealrState& operator=(const AnnealrState&) = delete;

    /// Internal: apply parsed status to subjects. Runs on LVGL thread.
    void apply_status(const std::string& state_str,
                      const std::string& profile_name,
                      int stage_index, int stage_count,
                      const std::string& stage_label,
                      float stage_target,
                      float progress,
                      float elapsed_s, float remaining_s,
                      float run_elapsed_s);

    /// Build a human-readable status summary string.
    void update_status_text(const std::string& state_str,
                            const std::string& profile_name,
                            const std::string& stage_label,
                            float progress, float remaining_s,
                            float run_elapsed_s);

    // Parse helpers
    static AnnealrSegment parse_segment_line(const std::string& line,
                                             float prev_target);

    bool initialized_ = false;
    bool available_    = false;

    // Subjects
    lv_subject_t state_{};
    lv_subject_t profile_name_{};
    lv_subject_t stage_label_{};
    lv_subject_t stage_index_{};
    lv_subject_t stage_count_{};
    lv_subject_t stage_target_{};  // centidegrees (int)
    lv_subject_t progress_{};     // 0..100 (int)
    lv_subject_t elapsed_s_{};    // segment elapsed (int seconds)
    lv_subject_t remaining_s_{};  // segment remaining (int seconds)
    lv_subject_t run_elapsed_s_{}; // total run elapsed (int seconds)
    lv_subject_t status_text_{};  // composite status string
    lv_subject_t profiles_version_{};

    // Static buffers for string subjects (must outlive subjects)
    char state_buf_[32]        = "idle";
    char profile_name_buf_[64] = "";
    char stage_label_buf_[128] = "";
    char status_text_buf_[512] = "";

    // Profile storage
    std::vector<AnnealrProfile> profiles_;
    mutable std::mutex profiles_mutex_;
};

}  // namespace helix
