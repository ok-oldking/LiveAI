"""Page scripts that track in-page audio playback for any provider."""
from __future__ import annotations

MEDIA_TRACKER_SCRIPT = """
() => {
    // Each Read aloud click starts a fresh session, including on reused audio.
    window.__liveGptReadAloudTracker = {
        media: null,
        playCount: 0,
        mediaCount: 0,
        playedTime: 0,
        lastTime: 0,
        source: ''
    };
    if (window.__liveGptMediaTrackerInstalled === 2) return;
    window.__liveGptMediaTrackerInstalled = 2;
    const sources = new Map();
    window.__liveGptMediaSources = sources;
    const originalCreateURL = URL.createObjectURL;
    URL.createObjectURL = function(object) {
        const url = originalCreateURL.call(this, object);
        if (typeof MediaSource !== 'undefined' && object instanceof MediaSource) {
            sources.set(url, new WeakRef(object));
            if (sources.size > 128) sources.delete(sources.keys().next().value);
        }
        return url;
    };
    const sample = () => {
        const tracker = window.__liveGptReadAloudTracker;
        const media = tracker.media;
        if (!media) return;
        const current = Number.isFinite(media.currentTime) ? media.currentTime : 0;
        const source = media.currentSrc || media.src || '';
        if (source !== tracker.source || (!media.seeking && current < tracker.lastTime - 0.05)) {
            tracker.mediaCount += 1;
            tracker.lastTime = 0;
        }
        // Only consumed audio advances the estimate; pauses and buffering do not.
        if (!media.seeking) tracker.playedTime += Math.max(current - tracker.lastTime, 0);
        tracker.lastTime = current;
        tracker.source = source;
    };
    window.__liveGptSampleMedia = sample;
    const observed = new WeakSet();
    const originalPlay = HTMLMediaElement.prototype.play;
    HTMLMediaElement.prototype.play = function(...args) {
        sample();
        const tracker = window.__liveGptReadAloudTracker;
        if (tracker.media !== this) {
            tracker.media = this;
            tracker.mediaCount += 1;
            tracker.lastTime = Number.isFinite(this.currentTime) ? this.currentTime : 0;
            tracker.source = this.currentSrc || this.src || '';
        }
        tracker.playCount += 1;
        if (!observed.has(this)) {
            observed.add(this);
            for (const event of ['timeupdate', 'pause', 'waiting', 'ended', 'emptied', 'seeking', 'seeked']) {
                this.addEventListener(event, () => {
                    if (window.__liveGptReadAloudTracker.media === this) sample();
                });
            }
        }
        return originalPlay.apply(this, args);
    };
}
"""
MEDIA_PROGRESS_SCRIPT = """
() => {
    const tracker = window.__liveGptReadAloudTracker;
    const media = tracker?.media ||
        [...document.querySelectorAll('audio, video')].find(item =>
            !item.paused || item.currentTime > 0
        );
    if (!media) return null;
    window.__liveGptSampleMedia?.();
    const source = window.__liveGptMediaSources?.get(media.currentSrc || media.src)?.deref();
    return {
        playCount: tracker?.playCount || 0,
        mediaCount: tracker?.mediaCount || 0,
        playedTime: tracker?.playedTime || 0,
        // A growing MediaSource duration describes the buffered prefix only.
        durationIsFinal: !source || source.readyState === 'ended',
        currentTime: Number.isFinite(media.currentTime)
            ? media.currentTime : 0,
        duration: Number.isFinite(media.duration)
            ? media.duration : null,
        paused: media.paused,
        ended: media.ended
    };
}
"""
MEDIA_STOP_SCRIPT = """
() => {
    // Read aloud can use an Audio object outside the DOM.
    const mediaElements = new Set([
        window.__liveGptReadAloudTracker?.media,
        ...document.querySelectorAll('audio, video')
    ]);
    for (const media of mediaElements) {
        if (!media) continue;
        media.pause();
        try { media.currentTime = 0; } catch (_) {}
    }
}
"""

__all__ = ["MEDIA_PROGRESS_SCRIPT", "MEDIA_STOP_SCRIPT", "MEDIA_TRACKER_SCRIPT"]
