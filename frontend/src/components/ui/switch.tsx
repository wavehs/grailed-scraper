'use client';

import * as RadixSwitch from '@radix-ui/react-switch';

/** Linear's small toggle: a 28×16 track with a sliding knob, named by `label`. */
export function Switch({
  label,
  checked,
  onChange,
  disabled,
  id,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  id?: string;
}) {
  return (
    <RadixSwitch.Root
      id={id}
      aria-label={label}
      checked={checked}
      disabled={disabled}
      onCheckedChange={onChange}
      className="relative inline-flex h-4 w-7 shrink-0 items-center rounded-full border border-[var(--border-default)] bg-[var(--bg-control)] transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:border-[var(--accent)] data-[state=checked]:bg-[var(--accent)]"
    >
      <RadixSwitch.Thumb className="block h-3 w-3 translate-x-px rounded-full bg-[var(--text-secondary)] transition-transform duration-100 data-[state=checked]:translate-x-[13px] data-[state=checked]:bg-white" />
    </RadixSwitch.Root>
  );
}
