# annealr: KlipperScreen custom panel
#
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# File: panels/annealr.py
# Description: Touchscreen dashboard for annealr annealing/drying
#              controller. Shows profile selection with temperature
#              curve preview, live run monitoring with actual vs
#              planned temperature chart, and context-aware controls.
#
# Layout (800x480):
#   Left (~280px):  Profile list, action buttons, status info
#   Right (~520px): Temperature chart (Cairo)
#   Bottom bar:     Last console line with timestamp

import logging
import math
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Pango, GLib

from ks_includes.screen_panel import ScreenPanel

logger = logging.getLogger("KlipperScreen.annealr")

# Chart colors (RGBA)
COLOR_BG = (0.12, 0.12, 0.14, 1.0)
COLOR_GRID = (0.25, 0.25, 0.28, 1.0)
COLOR_GRID_TEXT = (0.5, 0.5, 0.55, 1.0)
COLOR_PLANNED = (0.4, 0.7, 1.0, 0.6)
COLOR_PLANNED_FUTURE = (0.4, 0.7, 1.0, 0.3)
COLOR_ACTUAL = (1.0, 0.45, 0.15, 1.0)
COLOR_MARKER = (0.2, 1.0, 0.4, 0.9)
COLOR_AMBIENT = (0.5, 0.5, 0.5, 0.3)
COLOR_TARGET_LINE = (1.0, 1.0, 0.3, 0.3)

# Chart margins (px)
CHART_MARGIN_LEFT = 45
CHART_MARGIN_RIGHT = 10
CHART_MARGIN_TOP = 15
CHART_MARGIN_BOTTOM = 30

# Update interval for live chart (ms)
CHART_UPDATE_MS = 2000


class Panel(ScreenPanel):
    """Annealr annealing controller panel for KlipperScreen."""

    def __init__(self, screen, title):
        title = title or "Annealing"
        super().__init__(screen, title)

        # State
        self._profiles = {}
        self._selected_profile = None
        self._annealr_status = {}
        self._temp_history = []       # [(wall_time, temp_c)]
        self._run_start_wall = None
        self._last_console_msg = ""
        self._last_console_time = 0

        # Build UI
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Top area: left controls + right chart
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top_box.set_vexpand(True)

        # Left panel
        left_box = self._build_left_panel()
        left_box.set_size_request(280, -1)
        top_box.pack_start(left_box, False, False, 0)

        # Separator
        top_box.pack_start(Gtk.Separator(
            orientation=Gtk.Orientation.VERTICAL), False, False, 2)

        # Right panel: chart
        self._chart = Gtk.DrawingArea()
        self._chart.set_hexpand(True)
        self._chart.set_vexpand(True)
        self._chart.connect("draw", self._on_chart_draw)
        top_box.pack_start(self._chart, True, True, 0)

        main_box.pack_start(top_box, True, True, 0)

        # Bottom bar: console line
        main_box.pack_start(Gtk.Separator(), False, False, 0)
        self._console_label = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self._console_label.set_margin_start(5)
        self._console_label.set_margin_end(5)
        self._console_label.get_style_context().add_class("dim-label")
        main_box.pack_start(self._console_label, False, False, 2)

        self.content.add(main_box)

        # Discover profiles from config
        self._discover_profiles()

        # Periodic chart refresh
        self._update_timer = GLib.timeout_add(CHART_UPDATE_MS, self._on_timer)

    def _build_left_panel(self):
        """Build the left control panel with profile list and buttons."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_start(5)
        box.set_margin_end(5)
        box.set_margin_top(5)

        # Profile list header
        header = Gtk.Label(xalign=0)
        header.set_markup("<b>Profiles</b>")
        box.pack_start(header, False, False, 0)

        # Profile list (scrollable)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_min_content_height(120)

        self._profile_listbox = Gtk.ListBox()
        self._profile_listbox.set_selection_mode(
            Gtk.SelectionMode.SINGLE)
        self._profile_listbox.connect(
            "row-selected", self._on_profile_selected)
        scroll.add(self._profile_listbox)
        box.pack_start(scroll, True, True, 0)

        # Status info area
        self._status_label = Gtk.Label(
            xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD)
        self._status_label.set_markup("<small>Idle</small>")
        box.pack_start(self._status_label, False, False, 5)

        # Action buttons
        btn_grid = Gtk.Grid(
            column_spacing=5, row_spacing=5,
            column_homogeneous=True)

        self._btn_start = self._gtk.Button(
            "resume", "Start", "color1")
        self._btn_start.connect("clicked", self._on_start)
        self._btn_start.set_sensitive(False)
        btn_grid.attach(self._btn_start, 0, 0, 1, 1)

        self._btn_pause = self._gtk.Button(
            "pause", "Pause", "color2")
        self._btn_pause.connect("clicked", self._on_pause)
        self._btn_pause.set_sensitive(False)
        btn_grid.attach(self._btn_pause, 1, 0, 1, 1)

        self._btn_resume = self._gtk.Button(
            "resume", "Resume", "color3")
        self._btn_resume.connect("clicked", self._on_resume)
        self._btn_resume.set_sensitive(False)
        btn_grid.attach(self._btn_resume, 0, 1, 1, 1)

        self._btn_cancel = self._gtk.Button(
            "stop", "Cancel", "color4")
        self._btn_cancel.connect("clicked", self._on_cancel)
        self._btn_cancel.set_sensitive(False)
        btn_grid.attach(self._btn_cancel, 1, 1, 1, 1)

        box.pack_start(btn_grid, False, False, 5)

        return box

    # -- Profile discovery -------------------------------------------------

    def _discover_profiles(self):
        """Find annealr_profile sections from printer config."""
        self._profiles = {}
        try:
            sections = self._printer.get_config_section_list(
                "annealr_profile ")
            for section in sections:
                name = section.split(None, 1)[1] if " " in section else section
                cfg = self._printer.get_config_section(section)
                self._profiles[name] = self._parse_profile_config(
                    name, cfg)
        except Exception as e:
            logger.warning("Could not discover profiles: %s", e)

        self._populate_profile_list()

    def _parse_profile_config(self, name, cfg):
        """Parse a profile config section into a dict for chart rendering."""
        profile = {
            'name': name,
            'description': cfg.get('description', ''),
            'segments': [],
        }
        segments_raw = cfg.get('segments', '')
        prev_target = None
        for line in segments_raw.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            seg = self._parse_segment_line(line, prev_target)
            if seg:
                profile['segments'].append(seg)
                if seg['kind'] == 'ramp':
                    prev_target = seg['target']
        return profile

    @staticmethod
    def _parse_segment_line(line, prev_target):
        """Parse a segment line into a dict."""
        parts = [p.strip() for p in line.split(',')]
        if len(parts) < 2:
            return None
        kind = parts[0].lower()
        try:
            value = float(parts[1])
        except ValueError:
            return None

        rate = None
        duration = None
        for part in parts[2:]:
            part = part.strip()
            if part.startswith('rate='):
                try:
                    rate = float(part[5:])
                except ValueError:
                    pass
            elif part.startswith('duration='):
                try:
                    duration = float(part[9:])
                except ValueError:
                    pass

        if kind == 'ramp':
            duration_s = duration * 60.0 if duration is not None else None
            return {
                'kind': 'ramp', 'target': value,
                'duration_s': duration_s, 'rate': rate}
        elif kind == 'soak':
            return {
                'kind': 'soak', 'target': prev_target or 0,
                'duration_s': value * 60.0, 'rate': None}
        return None

    def _populate_profile_list(self):
        """Fill the profile listbox with discovered profiles."""
        for child in self._profile_listbox.get_children():
            self._profile_listbox.remove(child)

        for name in sorted(self._profiles.keys()):
            profile = self._profiles[name]
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.set_margin_start(5)
            box.set_margin_end(5)
            box.set_margin_top(3)
            box.set_margin_bottom(3)

            name_label = Gtk.Label(xalign=0)
            name_label.set_markup("<b>%s</b>" % name)
            box.pack_start(name_label, False, False, 0)

            if profile['description']:
                desc = Gtk.Label(
                    xalign=0, ellipsize=Pango.EllipsizeMode.END)
                desc.set_markup(
                    "<small>%s</small>" % profile['description'])
                box.pack_start(desc, False, False, 0)

            est = self._estimate_profile_duration(profile)
            if est > 0:
                mins = int(est / 60)
                hours = mins // 60
                rem = mins % 60
                if hours > 0:
                    dur_text = "~%dh %dm" % (hours, rem)
                else:
                    dur_text = "~%dm" % mins
                dur_label = Gtk.Label(xalign=0)
                dur_label.set_markup("<small>%s</small>" % dur_text)
                box.pack_start(dur_label, False, False, 0)

            row.add(box)
            row.profile_name = name
            self._profile_listbox.add(row)

        self._profile_listbox.show_all()

    @staticmethod
    def _estimate_profile_duration(profile, start_temp=22.0):
        """Estimate total profile duration in seconds."""
        total = 0.0
        current = start_temp
        for seg in profile['segments']:
            if seg['kind'] == 'ramp':
                if seg['duration_s'] is not None:
                    total += seg['duration_s']
                elif seg['rate'] is not None and seg['rate'] > 0:
                    delta = abs(seg['target'] - current)
                    total += (delta / seg['rate']) * 60.0
                current = seg['target']
            elif seg['kind'] == 'soak':
                if seg['duration_s']:
                    total += seg['duration_s']
        return total

    # -- Button handlers ---------------------------------------------------

    def _on_profile_selected(self, listbox, row):
        """Handle profile selection."""
        if row is None:
            self._selected_profile = None
            self._btn_start.set_sensitive(False)
        else:
            self._selected_profile = row.profile_name
            state = self._annealr_status.get('state', 'idle')
            self._btn_start.set_sensitive(state == 'idle')
        self._chart.queue_draw()

    def _on_start(self, widget):
        if self._selected_profile:
            self._screen._ws.klippy.gcode_script(
                "ANNEALR_START PROFILE=%s" % self._selected_profile)
            self._temp_history = []
            self._run_start_wall = time.monotonic()

    def _on_pause(self, widget):
        self._screen._ws.klippy.gcode_script("ANNEALR_PAUSE")

    def _on_resume(self, widget):
        self._screen._ws.klippy.gcode_script("ANNEALR_RESUME")

    def _on_cancel(self, widget):
        self._screen._ws.klippy.gcode_script("ANNEALR_CANCEL")

    # -- Status updates ----------------------------------------------------

    def process_update(self, action, data):
        """Receive updates from KlipperScreen."""
        if action == "notify_status_update":
            if "annealr" in data:
                self._annealr_status.update(data["annealr"])
                self._update_ui_state()
                self._chart.queue_draw()

            self._record_temperature(data)

        elif action == "notify_gcode_response":
            if data and not data.startswith("//"):
                self._last_console_msg = data
                self._last_console_time = time.time()
                ts = time.strftime(
                    "%H:%M:%S", time.localtime(self._last_console_time))
                self._console_label.set_text("[%s] %s" % (ts, data))

    def _record_temperature(self, data):
        """Extract chamber temperature from status update."""
        for key in data:
            if key.startswith("heater_generic"):
                temp = data[key].get("temperature")
                if temp is not None:
                    now = time.monotonic()
                    self._temp_history.append((now, temp))
                    cutoff = now - 7200
                    while (self._temp_history
                           and self._temp_history[0][0] < cutoff):
                        self._temp_history.pop(0)
                    break

    def _update_ui_state(self):
        """Update button sensitivity and status label from annealr state."""
        state = self._annealr_status.get('state', 'idle')
        profile_name = self._annealr_status.get('profile', None)

        is_idle = state == 'idle'
        is_running = state in ('ramping', 'soaking', 'cooling')
        is_paused = state == 'paused'

        self._btn_start.set_sensitive(
            is_idle and self._selected_profile is not None)
        self._btn_pause.set_sensitive(is_running)
        self._btn_resume.set_sensitive(is_paused)
        self._btn_cancel.set_sensitive(is_running or is_paused)

        parts = ["<b>%s</b>" % state.capitalize()]
        if profile_name:
            parts.append("Profile: %s" % profile_name)

        stage = self._annealr_status.get('stage')
        if stage:
            label = stage.get('label', '')
            progress = stage.get('progress', 0)
            remaining = stage.get('remaining_s', 0)
            parts.append("%s" % label)
            if progress > 0:
                parts.append("%.0f%%" % (progress * 100))
            if remaining > 0:
                rm = int(remaining / 60)
                parts.append("%dm remaining" % rm)

        run_elapsed = self._annealr_status.get('run_elapsed_s', 0)
        if run_elapsed > 0:
            em = int(run_elapsed / 60)
            eh = em // 60
            if eh > 0:
                parts.append("Elapsed: %dh %dm" % (eh, em % 60))
            else:
                parts.append("Elapsed: %dm" % em)

        self._status_label.set_markup(
            "<small>%s</small>" % "\n".join(parts))

        if is_running and self._run_start_wall is None:
            self._run_start_wall = time.monotonic()
        elif is_idle:
            self._run_start_wall = None

    # -- Timer -------------------------------------------------------------

    def _on_timer(self):
        """Periodic refresh for chart smoothness."""
        if self._annealr_status.get('state', 'idle') != 'idle':
            self._chart.queue_draw()
        return True

    # -- Chart drawing -----------------------------------------------------

    def _on_chart_draw(self, widget, cr):
        """Main chart draw handler using Cairo."""
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height

        # Background
        cr.set_source_rgba(*COLOR_BG)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        cx = CHART_MARGIN_LEFT
        cy = CHART_MARGIN_TOP
        cw = w - CHART_MARGIN_LEFT - CHART_MARGIN_RIGHT
        ch = h - CHART_MARGIN_TOP - CHART_MARGIN_BOTTOM

        if cw < 50 or ch < 50:
            return

        state = self._annealr_status.get('state', 'idle')
        is_running = state in ('ramping', 'soaking', 'cooling', 'paused')

        if is_running:
            self._draw_live_chart(cr, cx, cy, cw, ch)
        elif self._selected_profile:
            profile = self._profiles.get(self._selected_profile)
            if profile:
                self._draw_profile_preview(cr, cx, cy, cw, ch, profile)
        else:
            cr.set_source_rgba(*COLOR_GRID_TEXT)
            cr.select_font_face("monospace", 0, 0)
            cr.set_font_size(14)
            text = "Select a profile"
            extents = cr.text_extents(text)
            cr.move_to(cx + cw / 2 - extents.width / 2, cy + ch / 2)
            cr.show_text(text)

    def _draw_profile_preview(self, cr, cx, cy, cw, ch, profile):
        """Draw a static temperature profile preview."""
        if not profile['segments']:
            return

        points = self._build_planned_curve(profile, start_temp=22.0)
        if not points:
            return

        t_min = min(p[1] for p in points) - 10
        t_max = max(p[1] for p in points) + 10
        t_min = max(0, t_min)
        time_max = points[-1][0]
        if time_max <= 0:
            time_max = 60

        self._draw_grid(cr, cx, cy, cw, ch, 0, time_max, t_min, t_max)

        # Planned profile line
        cr.set_source_rgba(*COLOR_PLANNED)
        cr.set_line_width(2.5)
        first = True
        for t_s, temp in points:
            px = cx + (t_s / time_max) * cw
            py = cy + ch - ((temp - t_min) / (t_max - t_min)) * ch
            if first:
                cr.move_to(px, py)
                first = False
            else:
                cr.line_to(px, py)
        cr.stroke()

        # Segment labels
        self._draw_segment_labels(
            cr, cx, cy, cw, ch, profile, points, 0, time_max, t_min, t_max)

    def _draw_live_chart(self, cr, cx, cy, cw, ch):
        """Draw the live run chart with planned + actual traces."""
        profile_name = self._annealr_status.get('profile')
        profile = self._profiles.get(profile_name) if profile_name else None

        now = time.monotonic()
        elapsed = (now - self._run_start_wall) if self._run_start_wall else 0

        planned_points = []
        if profile:
            planned_points = self._build_dynamic_planned_curve(
                profile, elapsed)

        all_temps = ([p[1] for p in self._temp_history]
                     if self._temp_history else [22])
        if planned_points:
            all_temps.extend(p[1] for p in planned_points)

        t_min = min(all_temps) - 10
        t_max = max(all_temps) + 10
        t_min = max(0, t_min)

        time_max = elapsed
        if planned_points:
            time_max = max(time_max, planned_points[-1][0])
        time_max = max(time_max, 120)
        time_max *= 1.05

        self._draw_grid(cr, cx, cy, cw, ch, 0, time_max, t_min, t_max)

        # Planned profile (split past/future colors)
        if planned_points and len(planned_points) >= 2:
            cr.set_line_width(1.5)
            for i in range(1, len(planned_points)):
                t0, temp0 = planned_points[i - 1]
                t1, temp1 = planned_points[i]
                mid_t = (t0 + t1) / 2
                if mid_t <= elapsed:
                    cr.set_source_rgba(*COLOR_PLANNED)
                else:
                    cr.set_source_rgba(*COLOR_PLANNED_FUTURE)
                px0 = cx + (t0 / time_max) * cw
                py0 = cy + ch - ((temp0 - t_min) / (t_max - t_min)) * ch
                px1 = cx + (t1 / time_max) * cw
                py1 = cy + ch - ((temp1 - t_min) / (t_max - t_min)) * ch
                cr.move_to(px0, py0)
                cr.line_to(px1, py1)
                cr.stroke()

        # Actual temperature trace
        if self._temp_history and self._run_start_wall:
            cr.set_source_rgba(*COLOR_ACTUAL)
            cr.set_line_width(2.5)
            first = True
            for wall_t, temp in self._temp_history:
                t_s = wall_t - self._run_start_wall
                if t_s < 0:
                    continue
                px = cx + (t_s / time_max) * cw
                py = cy + ch - ((temp - t_min) / (t_max - t_min)) * ch
                if first:
                    cr.move_to(px, py)
                    first = False
                else:
                    cr.line_to(px, py)
            cr.stroke()

        # Current position marker (vertical dashed line)
        if elapsed > 0:
            px = cx + (elapsed / time_max) * cw
            cr.set_source_rgba(*COLOR_MARKER)
            cr.set_line_width(1)
            cr.set_dash([4, 4])
            cr.move_to(px, cy)
            cr.line_to(px, cy + ch)
            cr.stroke()
            cr.set_dash([])

        # Current target (horizontal dashed line)
        stage = self._annealr_status.get('stage')
        if stage and stage.get('target'):
            target = stage['target']
            py = cy + ch - ((target - t_min) / (t_max - t_min)) * ch
            cr.set_source_rgba(*COLOR_TARGET_LINE)
            cr.set_line_width(1)
            cr.set_dash([6, 4])
            cr.move_to(cx, py)
            cr.line_to(cx + cw, py)
            cr.stroke()
            cr.set_dash([])

    # -- Chart data helpers ------------------------------------------------

    def _build_planned_curve(self, profile, start_temp=22.0):
        """Build (time_s, temp) points for a static profile preview."""
        points = [(0, start_temp)]
        current_time = 0
        current_temp = start_temp

        for seg in profile['segments']:
            if seg['kind'] == 'ramp':
                target = seg['target']
                if seg['duration_s'] is not None:
                    dur = seg['duration_s']
                elif seg['rate'] is not None and seg['rate'] > 0:
                    delta = abs(target - current_temp)
                    dur = (delta / seg['rate']) * 60.0
                else:
                    dur = 0  # unconstrained: vertical step

                if dur > 0:
                    current_time += dur
                points.append((current_time, target))
                current_temp = target

            elif seg['kind'] == 'soak':
                dur = seg.get('duration_s', 0) or 0
                if dur > 0:
                    current_time += dur
                    points.append((current_time, current_temp))

        return points

    def _build_dynamic_planned_curve(self, profile, elapsed):
        """Build planned curve adapted to actual run timing.

        Completed segments use estimated actual time.
        Current and future segments use planned durations.
        """
        stage_index = self._annealr_status.get('stage_index', 0)
        stage = self._annealr_status.get('stage')

        points = []
        current_time = 0
        current_temp = 22.0

        if self._temp_history:
            current_temp = self._temp_history[0][1]

        points.append((0, current_temp))

        for i, seg in enumerate(profile['segments']):
            if seg['kind'] == 'ramp':
                target = seg['target']

                if i < stage_index:
                    # Completed: estimate duration
                    if seg['duration_s'] is not None:
                        dur = seg['duration_s']
                    elif seg['rate'] and seg['rate'] > 0:
                        dur = abs(target - current_temp) / seg['rate'] * 60
                    else:
                        dur = 0
                elif i == stage_index and stage:
                    seg_elapsed = stage.get('elapsed_s', 0)
                    remaining = stage.get('remaining_s', 0)
                    dur = seg_elapsed + remaining
                else:
                    if seg['duration_s'] is not None:
                        dur = seg['duration_s']
                    elif seg['rate'] and seg['rate'] > 0:
                        dur = abs(target - current_temp) / seg['rate'] * 60
                    else:
                        dur = 0

                if dur > 0:
                    current_time += dur
                points.append((current_time, target))
                current_temp = target

            elif seg['kind'] == 'soak':
                dur = seg.get('duration_s', 0) or 0
                if i == stage_index and stage:
                    seg_elapsed = stage.get('elapsed_s', 0)
                    remaining = stage.get('remaining_s', 0)
                    dur = seg_elapsed + remaining

                if dur > 0:
                    current_time += dur
                    points.append((current_time, current_temp))

        return points

    # -- Grid and axis drawing ---------------------------------------------

    def _draw_grid(self, cr, cx, cy, cw, ch, time_min, time_max,
                   temp_min, temp_max):
        """Draw temperature and time grid lines with labels."""
        cr.select_font_face("monospace", 0, 0)
        cr.set_font_size(10)

        temp_range = temp_max - temp_min
        if temp_range <= 0:
            return

        # Temperature ticks (horizontal lines)
        temp_step = self._nice_step(temp_range, 5)
        t_tick = math.ceil(temp_min / temp_step) * temp_step
        while t_tick <= temp_max:
            py = cy + ch - ((t_tick - temp_min) / temp_range) * ch
            cr.set_source_rgba(*COLOR_GRID)
            cr.set_line_width(0.5)
            cr.move_to(cx, py)
            cr.line_to(cx + cw, py)
            cr.stroke()
            cr.set_source_rgba(*COLOR_GRID_TEXT)
            cr.move_to(cx - 40, py + 4)
            cr.show_text("%d\u00b0C" % int(t_tick))
            t_tick += temp_step

        # Time ticks (vertical lines)
        time_range = time_max - time_min
        if time_range <= 0:
            return

        time_step_s = self._nice_time_step(time_range, 6)
        t_tick = math.ceil(time_min / time_step_s) * time_step_s
        while t_tick <= time_max:
            px = cx + ((t_tick - time_min) / time_range) * cw
            cr.set_source_rgba(*COLOR_GRID)
            cr.set_line_width(0.5)
            cr.move_to(px, cy)
            cr.line_to(px, cy + ch)
            cr.stroke()
            cr.set_source_rgba(*COLOR_GRID_TEXT)
            mins = int(t_tick / 60)
            if mins >= 60:
                label = "%dh%dm" % (mins // 60, mins % 60)
            else:
                label = "%dm" % mins
            extents = cr.text_extents(label)
            cr.move_to(px - extents.width / 2, cy + ch + 15)
            cr.show_text(label)
            t_tick += time_step_s

        # Chart border
        cr.set_source_rgba(*COLOR_GRID)
        cr.set_line_width(1)
        cr.rectangle(cx, cy, cw, ch)
        cr.stroke()

    def _draw_segment_labels(self, cr, cx, cy, cw, ch, profile, points,
                             time_min, time_max, temp_min, temp_max):
        """Draw small labels at segment transitions on the preview."""
        cr.set_source_rgba(1, 1, 1, 0.6)
        cr.set_font_size(9)

        current_temp = points[0][1] if points else 22
        current_time = 0

        for seg in profile['segments']:
            if seg['kind'] == 'ramp':
                target = seg['target']
                if seg['duration_s'] is not None:
                    dur = seg['duration_s']
                elif seg['rate'] and seg['rate'] > 0:
                    dur = abs(target - current_temp) / seg['rate'] * 60
                else:
                    dur = 0

                end_time = current_time + dur
                mid_time = current_time + dur / 2 if dur > 0 else current_time
                mid_temp = (current_temp + target) / 2

                px = cx + (mid_time / time_max) * cw
                py = cy + ch - (
                    (mid_temp - temp_min) / (temp_max - temp_min)) * ch

                cr.move_to(px + 3, py - 3)
                cr.show_text("%.0f\u00b0C" % target)

                current_time = end_time
                current_temp = target

            elif seg['kind'] == 'soak':
                dur = seg.get('duration_s', 0) or 0
                mid_time = current_time + dur / 2
                px = cx + (mid_time / time_max) * cw
                py = cy + ch - (
                    (current_temp - temp_min) / (temp_max - temp_min)) * ch

                mins = int(dur / 60)
                cr.move_to(px + 3, py - 3)
                cr.show_text("%dm soak" % mins)

                current_time += dur

    @staticmethod
    def _nice_step(data_range, target_ticks):
        """Compute a nice round step size for axis ticks."""
        raw = data_range / max(target_ticks, 1)
        magnitude = 10 ** math.floor(math.log10(max(raw, 0.001)))
        residual = raw / magnitude
        if residual <= 1.5:
            return magnitude
        elif residual <= 3.5:
            return 2 * magnitude
        elif residual <= 7.5:
            return 5 * magnitude
        return 10 * magnitude

    @staticmethod
    def _nice_time_step(time_range_s, target_ticks):
        """Compute a nice round time step for the time axis."""
        raw = time_range_s / max(target_ticks, 1)
        candidates = [
            30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200]
        for c in candidates:
            if c >= raw * 0.7:
                return c
        return candidates[-1]

    # -- Cleanup -----------------------------------------------------------

    def on_destroy(self):
        """Clean up timer when panel is destroyed."""
        if hasattr(self, '_update_timer'):
            GLib.source_remove(self._update_timer)
