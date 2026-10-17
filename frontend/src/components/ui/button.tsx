import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'success';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: ReactNode;
  size?: 'sm' | 'md';
}

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    'bg-[var(--accent)] text-[var(--accent-contrast)] shadow-[inset_0_1px_0_rgba(255,255,255,0.12)] hover:bg-[var(--accent-hover)]',
  secondary:
    'border border-[var(--border-default)] bg-[var(--bg-control)] text-[var(--text-primary)] hover:bg-[var(--bg-control-hover)]',
  danger:
    'border border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)] hover:border-[var(--danger)]',
  ghost:
    'text-[var(--text-secondary)] hover:bg-[var(--bg-control)] hover:text-[var(--text-primary)]',
  success:
    'border border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success)] hover:border-[var(--success)]',
};

/** Linear's control heights: 28px in toolbars and rows, 32px for form actions. */
const sizeClasses = {
  sm: 'h-7 px-2 text-xs gap-1.5',
  md: 'h-8 px-3 text-[13px] gap-2',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', icon, size = 'md', children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-[5px] font-medium transition-colors duration-100 disabled:cursor-not-allowed disabled:opacity-40',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    >
      {icon && <span className="inline-flex shrink-0">{icon}</span>}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
