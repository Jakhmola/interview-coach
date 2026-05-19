import { useEffect, useRef, useState, type ReactNode } from "react";

type Props = {
  /** Called on the second (confirming) click. */
  onConfirm: () => void | Promise<void>;
  /** Default label. */
  label: ReactNode;
  /** Label shown in the "armed" state. */
  armedLabel?: ReactNode;
  /** How long the armed state persists before reverting (ms). */
  armedFor?: number;
  /** Optional className override for both states. */
  className?: string;
  /** Disable the button entirely. */
  disabled?: boolean;
  /** Optional icon node rendered before the label. */
  icon?: ReactNode;
  /** Pass-through HTML title for accessibility tooltips. */
  title?: string;
  /** Phase 22: optional one-liner shown next to the armed label so the
   * user can see the cascade before the destructive click goes through
   * (e.g. "Will clear your profile and unmap 3 supporting docs"). */
  consequenceLabel?: ReactNode;
};

/**
 * Two-step destructive-action button. First click "arms" the button: it
 * turns red, swaps label to `armedLabel` for `armedFor` ms, then reverts.
 * Second click within that window fires `onConfirm`.
 *
 * No modals, no shared dialog component. The morph is the warning.
 */
export function ArmedDeleteButton({
  onConfirm,
  label,
  armedLabel = "Click again to confirm",
  armedFor = 3000,
  className = "ghost-button danger",
  disabled = false,
  icon,
  title,
  consequenceLabel,
}: Props) {
  const [armed, setArmed] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  const disarm = () => {
    setArmed(false);
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const handleClick = () => {
    if (disabled) return;
    if (!armed) {
      setArmed(true);
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setArmed(false);
        timerRef.current = null;
      }, armedFor);
      return;
    }
    disarm();
    void onConfirm();
  };

  return (
    <button
      type="button"
      className={`${className} ${armed ? "armed" : ""}`.trim()}
      onClick={handleClick}
      disabled={disabled}
      title={title}
      aria-pressed={armed}
    >
      {icon}
      <span>{armed ? armedLabel : label}</span>
      {armed && consequenceLabel ? (
        <span className="armed-consequence">{consequenceLabel}</span>
      ) : null}
    </button>
  );
}
