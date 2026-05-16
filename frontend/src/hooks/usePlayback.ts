import { useCallback, useEffect, useRef, useState } from "react";

export interface UsePlaybackResult {
  currentTime: number;
  isPlaying: boolean;
  duration: number;
  play: () => void;
  pause: () => void;
  seek: (time: number) => void;
  setDuration: (d: number) => void;
  audioRef: React.RefObject<HTMLAudioElement | null>;
}

/**
 * Shared playback hook — manages current time, play/pause, and seek.
 * The audioRef should be attached to an <audio> element by the PreviewPlayer.
 * VideoCanvas reads currentTime to decide which subtitles are visible.
 */
export function usePlayback(): UsePlaybackResult {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const rafRef = useRef<number>(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [duration, setDuration] = useState(0);

  // Animation-frame loop to keep currentTime in sync while playing
  const tick = useCallback(() => {
    const audio = audioRef.current;
    if (audio && !audio.paused) {
      setCurrentTime(audio.currentTime);
      rafRef.current = requestAnimationFrame(tick);
    }
  }, []);

  const play = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.play().then(() => {
      setIsPlaying(true);
      rafRef.current = requestAnimationFrame(tick);
    }).catch(() => {
      // autoplay blocked — ignore
    });
  }, [tick]);

  const pause = useCallback(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.pause();
    setIsPlaying(false);
    cancelAnimationFrame(rafRef.current);
  }, []);

  const seek = useCallback((time: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    const clamped = Math.max(0, Math.min(time, audio.duration || duration));
    audio.currentTime = clamped;
    setCurrentTime(clamped);
  }, [duration]);

  // Sync when audio ends
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    const onEnded = () => {
      setIsPlaying(false);
      cancelAnimationFrame(rafRef.current);
    };
    const onTimeUpdate = () => {
      setCurrentTime(audio.currentTime);
    };
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("timeupdate", onTimeUpdate);
    return () => {
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("timeupdate", onTimeUpdate);
      cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return { currentTime, isPlaying, duration, play, pause, seek, setDuration, audioRef };
}
