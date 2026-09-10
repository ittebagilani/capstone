# vision

Owner: Itteba. Camera, ArUco homography, classifier, `scan()`. See CLAUDE.md §7 and
INTERFACES.md §1-2 for the pipeline plan and the exact `scan()` return schema.

Scheduled Aug 25-31 (CLAUDE.md §9). Until real `scan()` lands, `backend/fake_vision.py`
stands in — swapping it in is meant to be a one-line change (CLAUDE.md §3.3).
