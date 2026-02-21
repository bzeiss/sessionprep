"""Detail view subpackage: report, playback, and detail mixin."""

from .mixin import DetailMixin
from .report import render_track_detail_html, render_summary_html
from .playback import PlaybackController

__all__ = [
    "DetailMixin",
    "render_track_detail_html", "render_summary_html",
    "PlaybackController",
]
