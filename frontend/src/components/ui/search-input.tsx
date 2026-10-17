import { Search } from 'lucide-react';
import { cn } from '@/lib/utils';

/** The toolbar search box: a 28px input with a leading magnifier and a hidden label. */
export function SearchInput({
  label,
  placeholder,
  value,
  onChange,
  className,
}: {
  label: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <label className={cn('relative w-full sm:w-56', className)}>
      <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)]" aria-hidden />
      <span className="sr-only">{label}</span>
      <input
        className="control-sm w-full pl-7"
        placeholder={placeholder ?? label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </label>
  );
}
