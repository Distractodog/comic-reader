"""Reading statistics dialog — headline numbers plus a pages-per-day bar chart."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from library import Library

# Dark mode is locked on, so the chart pulls fixed palette colours.
_BAR = QColor("#c06060")
_BAR_EMPTY = QColor("#4a3535")
_AXIS = QColor("#9a7878")


class _PagesPerDayChart(QWidget):
    """Simple bar chart of pages read per day, drawn with paintEvent (no deps)."""

    def __init__(self, series: list[tuple[str, int]], parent=None):
        super().__init__(parent)
        self._series = series
        self.setMinimumHeight(180)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        w = self.width()
        h = self.height()
        pad_bottom = 18
        chart_h = h - pad_bottom
        n = len(self._series) or 1
        max_val = max((v for _, v in self._series), default=0)

        slot = w / n
        bar_w = max(2.0, slot * 0.7)
        for i, (date, val) in enumerate(self._series):
            x = i * slot + (slot - bar_w) / 2
            if max_val > 0 and val > 0:
                bar_h = (val / max_val) * (chart_h - 4)
                p.fillRect(
                    int(x), int(chart_h - bar_h), int(bar_w), int(bar_h), _BAR
                )
            else:
                # A faint baseline tick so empty days are still visible.
                p.fillRect(int(x), int(chart_h - 2), int(bar_w), 2, _BAR_EMPTY)

        # Axis line + first/last date labels.
        p.setPen(_AXIS)
        p.drawLine(0, chart_h, w, chart_h)
        if self._series:
            font = QFont(self.font())
            font.setPointSize(8)
            p.setFont(font)
            first = self._series[0][0][5:]   # MM-DD
            last = self._series[-1][0][5:]
            p.drawText(0, h - 4, first)
            p.drawText(w - 36, h - 4, last)
        p.end()


class StatsDialog(QDialog):
    def __init__(self, library: Library, parent=None):
        super().__init__(parent)
        self._library = library
        self.setWindowTitle("Reading Statistics")
        self.resize(560, 420)

        stats = library.get_stats()

        outer = QVBoxLayout(self)

        # Headline numbers in a small grid.
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        grid.setVerticalSpacing(10)

        def cell(row, col, value, label):
            v = QLabel(value)
            vf = QFont(v.font())
            vf.setPointSize(20)
            vf.setBold(True)
            v.setFont(vf)
            l = QLabel(label)
            l.setStyleSheet("color: #9a7878;")
            grid.addWidget(v, row * 2, col)
            grid.addWidget(l, row * 2 + 1, col)

        hours = stats["total_hours"]
        hours_str = f"{hours:.1f}" if hours < 100 else f"{int(hours)}"
        cell(0, 0, f"{stats['total_pages']:,}", "Pages read")
        cell(0, 1, hours_str, "Hours read")
        cell(0, 2, f"{stats['current_streak']}", "Day streak")
        cell(1, 0, f"{stats['comics_completed']}/{stats['total_comics']}", "Comics finished")
        cell(1, 1, f"{stats['completion_rate'] * 100:.0f}%", "Completion rate")
        outer.addLayout(grid)

        chart_label = QLabel("Pages per day — last 30 days")
        chart_label.setStyleSheet("color: #9a7878; margin-top: 8px;")
        outer.addWidget(chart_label)
        outer.addWidget(_PagesPerDayChart(stats["pages_per_day"]), 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)
