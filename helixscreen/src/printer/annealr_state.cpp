// annealr: HelixScreen integration
//
// Licensed under the GNU General Public License v3.0 (GPL-3.0)
// SPDX-License-Identifier: GPL-3.0-or-later
//
// File: annealr_state.cpp
// Description: AnnealrState implementation. Parses Moonraker status
//              updates from the annealr Klipper plugin and maintains
//              LVGL subjects for reactive UI binding.

#include "annealr_state.h"

#include <spdlog/spdlog.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <sstream>

// HelixScreen infrastructure
#include "ui_update_queue.h"
#include "static_subject_registry.h"

using json = nlohmann::json;

namespace helix {

// ── AnnealrProfile helpers ──────────────────────────────────────────────

float AnnealrProfile::estimate_duration_s(float start_temp) const {
    float total = 0.0f;
    float current = start_temp;

    for (const auto& seg : segments) {
        if (seg.kind == "ramp") {
            if (seg.duration_s > 0) {
                total += seg.duration_s;
            } else if (seg.rate > 0) {
                float delta = std::fabs(seg.target - current);
                total += (delta / seg.rate) * 60.0f;
            }
            // unconstrained ramp: contributes 0 (lower bound)
            current = seg.target;
        } else if (seg.kind == "soak") {
            total += seg.duration_s;
        }
    }
    return total;
}

std::vector<std::pair<float, float>> AnnealrProfile::build_planned_curve(
        float start_temp) const {
    std::vector<std::pair<float, float>> points;
    points.emplace_back(0.0f, start_temp);

    float t = 0.0f;
    float temp = start_temp;

    for (const auto& seg : segments) {
        if (seg.kind == "ramp") {
            float dur = 0.0f;
            if (seg.duration_s > 0) {
                dur = seg.duration_s;
            } else if (seg.rate > 0) {
                float delta = std::fabs(seg.target - temp);
                dur = (delta / seg.rate) * 60.0f;
            }
            // unconstrained: dur stays 0 → vertical step
            t += dur;
            points.emplace_back(t, seg.target);
            temp = seg.target;

        } else if (seg.kind == "soak") {
            float dur = seg.duration_s;
            if (dur > 0) {
                t += dur;
                points.emplace_back(t, temp);
            }
        }
    }
    return points;
}

// ── Singleton ───────────────────────────────────────────────────────────

AnnealrState& AnnealrState::instance() {
    static AnnealrState inst;
    return inst;
}

// ── Lifecycle ───────────────────────────────────────────────────────────

void AnnealrState::init_subjects() {
    if (initialized_) return;

    spdlog::info("[AnnealrState] Initializing subjects");

    // String subjects — static buffers, initial values
    std::strncpy(state_buf_, "idle", sizeof(state_buf_));
    std::memset(profile_name_buf_, 0, sizeof(profile_name_buf_));
    std::memset(stage_label_buf_, 0, sizeof(stage_label_buf_));
    std::strncpy(status_text_buf_, "Idle", sizeof(status_text_buf_));

    lv_subject_init_string(&state_, state_buf_, nullptr, sizeof(state_buf_), "idle");
    lv_subject_init_string(&profile_name_, profile_name_buf_, nullptr,
                           sizeof(profile_name_buf_), "");
    lv_subject_init_string(&stage_label_, stage_label_buf_, nullptr,
                           sizeof(stage_label_buf_), "");
    lv_subject_init_string(&status_text_, status_text_buf_, nullptr,
                           sizeof(status_text_buf_), "Idle");

    // Integer subjects
    lv_subject_init_int(&stage_index_, 0);
    lv_subject_init_int(&stage_count_, 0);
    lv_subject_init_int(&stage_target_, 0);
    lv_subject_init_int(&progress_, 0);
    lv_subject_init_int(&elapsed_s_, 0);
    lv_subject_init_int(&remaining_s_, 0);
    lv_subject_init_int(&run_elapsed_s_, 0);
    lv_subject_init_int(&profiles_version_, 0);

    // Register globally so XML bind_text/bind_value can find them
    lv_xml_register_subject(nullptr, "annealr_state", &state_);
    lv_xml_register_subject(nullptr, "annealr_profile_name", &profile_name_);
    lv_xml_register_subject(nullptr, "annealr_stage_label", &stage_label_);
    lv_xml_register_subject(nullptr, "annealr_stage_index", &stage_index_);
    lv_xml_register_subject(nullptr, "annealr_stage_count", &stage_count_);
    lv_xml_register_subject(nullptr, "annealr_stage_target", &stage_target_);
    lv_xml_register_subject(nullptr, "annealr_progress", &progress_);
    lv_xml_register_subject(nullptr, "annealr_elapsed_s", &elapsed_s_);
    lv_xml_register_subject(nullptr, "annealr_remaining_s", &remaining_s_);
    lv_xml_register_subject(nullptr, "annealr_run_elapsed_s", &run_elapsed_s_);
    lv_xml_register_subject(nullptr, "annealr_status_text", &status_text_);
    lv_xml_register_subject(nullptr, "annealr_profiles_version", &profiles_version_);

    initialized_ = true;

    // Self-register cleanup (co-located with init per HelixScreen pattern)
    StaticSubjectRegistry::instance().register_deinit(
        "AnnealrState", []() { AnnealrState::instance().deinit_subjects(); });
}

void AnnealrState::deinit_subjects() {
    if (!initialized_) return;

    spdlog::debug("[AnnealrState] Deinitializing subjects");

    lv_subject_deinit(&state_);
    lv_subject_deinit(&profile_name_);
    lv_subject_deinit(&stage_label_);
    lv_subject_deinit(&stage_index_);
    lv_subject_deinit(&stage_count_);
    lv_subject_deinit(&stage_target_);
    lv_subject_deinit(&progress_);
    lv_subject_deinit(&elapsed_s_);
    lv_subject_deinit(&remaining_s_);
    lv_subject_deinit(&run_elapsed_s_);
    lv_subject_deinit(&status_text_);
    lv_subject_deinit(&profiles_version_);

    initialized_ = false;
}

// ── Profile loading ─────────────────────────────────────────────────────

AnnealrSegment AnnealrState::parse_segment_line(const std::string& line,
                                                 float prev_target) {
    AnnealrSegment seg{};

    // Tokenize by comma
    std::vector<std::string> parts;
    std::istringstream iss(line);
    std::string token;
    while (std::getline(iss, token, ',')) {
        // trim whitespace
        auto start = token.find_first_not_of(" \t");
        auto end   = token.find_last_not_of(" \t");
        if (start != std::string::npos) {
            parts.push_back(token.substr(start, end - start + 1));
        }
    }

    if (parts.size() < 2) return seg;

    seg.kind = parts[0];
    // lowercase
    std::transform(seg.kind.begin(), seg.kind.end(), seg.kind.begin(), ::tolower);

    float value = 0;
    try { value = std::stof(parts[1]); }
    catch (...) { return seg; }

    seg.rate       = 0;
    seg.duration_s = 0;

    for (size_t i = 2; i < parts.size(); ++i) {
        const auto& p = parts[i];
        if (p.rfind("rate=", 0) == 0) {
            try { seg.rate = std::stof(p.substr(5)); } catch (...) {}
        } else if (p.rfind("duration=", 0) == 0) {
            try { seg.duration_s = std::stof(p.substr(9)) * 60.0f; } catch (...) {}
        }
    }

    if (seg.kind == "ramp") {
        seg.target = value;
    } else if (seg.kind == "soak") {
        seg.target     = prev_target;
        seg.duration_s = value * 60.0f;  // soak value is minutes
    }

    return seg;
}

void AnnealrState::load_profiles_from_config(const json& config) {
    std::lock_guard<std::mutex> lock(profiles_mutex_);
    profiles_.clear();

    // Iterate config sections looking for "annealr_profile *"
    for (auto& [section_name, section_data] : config.items()) {
        if (section_name.rfind("annealr_profile ", 0) != 0) continue;

        std::string name = section_name.substr(16);  // after "annealr_profile "

        AnnealrProfile profile;
        profile.name        = name;
        profile.description = section_data.value("description", "");

        std::string segments_raw = section_data.value("segments", "");
        float prev_target = 0;

        std::istringstream stream(segments_raw);
        std::string line;
        while (std::getline(stream, line)) {
            // Trim
            auto start = line.find_first_not_of(" \t\r\n");
            if (start == std::string::npos) continue;
            line = line.substr(start);
            auto end = line.find_last_not_of(" \t\r\n");
            if (end != std::string::npos) line.resize(end + 1);
            if (line.empty()) continue;

            AnnealrSegment seg = parse_segment_line(line, prev_target);
            if (seg.kind.empty()) continue;

            if (seg.kind == "ramp") {
                prev_target = seg.target;
            }
            profile.segments.push_back(std::move(seg));
        }

        if (!profile.segments.empty()) {
            spdlog::info("[AnnealrState] Loaded profile '{}' ({} segments)",
                         profile.name, profile.segments.size());
            profiles_.push_back(std::move(profile));
        }
    }

    // Sort by name for stable UI ordering
    std::sort(profiles_.begin(), profiles_.end(),
              [](const AnnealrProfile& a, const AnnealrProfile& b) {
                  return a.name < b.name;
              });

    // Bump version so UI rebuilds the profile list
    if (initialized_) {
        int v = lv_subject_get_int(&profiles_version_);
        lv_subject_set_int(&profiles_version_, v + 1);
    }
}

// ── Status updates ──────────────────────────────────────────────────────

void AnnealrState::update_from_status(const json& data) {
    // Parse on WebSocket thread (no LVGL calls)
    std::string state_str    = data.value("state", "idle");
    std::string profile_name = data.value("profile", "");
    int         stage_idx    = data.value("stage_index", 0);
    int         stage_cnt    = data.value("stage_count", 0);
    float       progress_f   = data.value("progress", 0.0f);
    float       run_elapsed  = data.value("run_elapsed_s", 0.0f);

    std::string stage_lbl;
    float       stage_tgt    = 0;
    float       seg_elapsed  = 0;
    float       seg_remaining = 0;

    if (data.contains("stage") && !data["stage"].is_null()) {
        const auto& stage = data["stage"];
        stage_lbl     = stage.value("label", "");
        stage_tgt     = stage.value("target", 0.0f);
        seg_elapsed   = stage.value("elapsed_s", 0.0f);
        seg_remaining = stage.value("remaining_s", 0.0f);

        // Stage-level progress overrides top-level if available
        if (stage.contains("progress")) {
            progress_f = stage.value("progress", 0.0f);
        }
    }

    // Defer subject writes to LVGL main thread
    helix::ui::queue_update(
        [this, state_str, profile_name, stage_idx, stage_cnt,
         stage_lbl, stage_tgt, progress_f, seg_elapsed,
         seg_remaining, run_elapsed]() {
            apply_status(state_str, profile_name,
                         stage_idx, stage_cnt,
                         stage_lbl, stage_tgt,
                         progress_f,
                         seg_elapsed, seg_remaining,
                         run_elapsed);
        });
}

void AnnealrState::apply_status(
        const std::string& state_str,
        const std::string& profile_name,
        int stage_index, int stage_count,
        const std::string& stage_label,
        float stage_target,
        float progress,
        float elapsed_s, float remaining_s,
        float run_elapsed_s) {

    // Update string subjects
    std::strncpy(state_buf_, state_str.c_str(), sizeof(state_buf_) - 1);
    state_buf_[sizeof(state_buf_) - 1] = '\0';
    lv_subject_copy_string(&state_, state_buf_);

    std::strncpy(profile_name_buf_, profile_name.c_str(),
                 sizeof(profile_name_buf_) - 1);
    profile_name_buf_[sizeof(profile_name_buf_) - 1] = '\0';
    lv_subject_copy_string(&profile_name_, profile_name_buf_);

    std::strncpy(stage_label_buf_, stage_label.c_str(),
                 sizeof(stage_label_buf_) - 1);
    stage_label_buf_[sizeof(stage_label_buf_) - 1] = '\0';
    lv_subject_copy_string(&stage_label_, stage_label_buf_);

    // Update integer subjects
    lv_subject_set_int(&stage_index_, stage_index);
    lv_subject_set_int(&stage_count_, stage_count);
    lv_subject_set_int(&stage_target_,
                       static_cast<int>(stage_target * 10));  // centidegrees
    lv_subject_set_int(&progress_,
                       static_cast<int>(progress * 100));     // 0..100
    lv_subject_set_int(&elapsed_s_,
                       static_cast<int>(elapsed_s));
    lv_subject_set_int(&remaining_s_,
                       static_cast<int>(remaining_s));
    lv_subject_set_int(&run_elapsed_s_,
                       static_cast<int>(run_elapsed_s));

    // Update composite status text
    update_status_text(state_str, profile_name, stage_label,
                       progress, remaining_s, run_elapsed_s);
}

void AnnealrState::update_status_text(
        const std::string& state_str,
        const std::string& profile_name,
        const std::string& stage_label,
        float progress,
        float remaining_s,
        float run_elapsed_s) {

    // Build multi-line status summary
    std::string state_cap = state_str;
    if (!state_cap.empty()) {
        state_cap[0] = static_cast<char>(std::toupper(state_cap[0]));
    }

    std::string text = state_cap;

    if (!profile_name.empty()) {
        text += "\n";
        text += profile_name;
    }

    if (!stage_label.empty()) {
        text += "\n";
        text += stage_label;
    }

    if (progress > 0) {
        char buf[32];
        std::snprintf(buf, sizeof(buf), "\n%.0f%%", progress * 100.0f);
        text += buf;
    }

    if (remaining_s > 0) {
        int rm = static_cast<int>(remaining_s / 60.0f);
        char buf[32];
        std::snprintf(buf, sizeof(buf), "\n%dm remaining", rm);
        text += buf;
    }

    if (run_elapsed_s > 0) {
        int em = static_cast<int>(run_elapsed_s / 60.0f);
        int eh = em / 60;
        char buf[64];
        if (eh > 0) {
            std::snprintf(buf, sizeof(buf), "\nElapsed: %dh %dm", eh, em % 60);
        } else {
            std::snprintf(buf, sizeof(buf), "\nElapsed: %dm", em);
        }
        text += buf;
    }

    std::strncpy(status_text_buf_, text.c_str(), sizeof(status_text_buf_) - 1);
    status_text_buf_[sizeof(status_text_buf_) - 1] = '\0';
    lv_subject_copy_string(&status_text_, status_text_buf_);
}

// ── Convenience queries ─────────────────────────────────────────────────

bool AnnealrState::is_run_active() const {
    return (std::strcmp(state_buf_, "ramping") == 0 ||
            std::strcmp(state_buf_, "soaking") == 0 ||
            std::strcmp(state_buf_, "cooling") == 0 ||
            std::strcmp(state_buf_, "paused") == 0);
}

bool AnnealrState::is_paused() const {
    return std::strcmp(state_buf_, "paused") == 0;
}

bool AnnealrState::can_start() const {
    return (std::strcmp(state_buf_, "idle") == 0 ||
            std::strcmp(state_buf_, "complete") == 0 ||
            std::strcmp(state_buf_, "cancelled") == 0 ||
            std::strcmp(state_buf_, "error") == 0);
}

}  // namespace helix
