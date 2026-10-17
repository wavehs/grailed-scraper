'use client';

import { CircleHelp } from 'lucide-react';
import { useId, useState } from 'react';

export function HelpTip({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        aria-describedby={tooltipId}
        aria-expanded={open}
        aria-label="Help"
        className="inline-flex h-6 w-6 items-center justify-center rounded text-[var(--text-muted)] transition-colors duration-100 hover:bg-[var(--bg-control)] hover:text-[var(--text-primary)]"
        data-label={label}
        onBlur={() => setOpen(false)}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen(true);
          }
        }}
        role="button"
        tabIndex={0}
      >
        <CircleHelp size={14} />
      </span>
      <span
        className={`pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-64 max-w-[calc(100vw-2rem)] -translate-x-1/2 rounded-md bg-[var(--bg-surface-raised)] px-2.5 py-1.5 text-left text-xs font-normal normal-case leading-[18px] tracking-normal text-[var(--text-body)] shadow-[var(--shadow-float)] transition-[opacity,transform] duration-100 ${
          open ? 'translate-y-0 opacity-100' : 'invisible translate-y-1 opacity-0'
        }`}
        id={tooltipId}
        role="tooltip"
      >
        {text}
      </span>
    </span>
  );
}
