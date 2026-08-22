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
    'border border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-contrast)] hover:border-[var(--accent-hover)] hover:bg-[var(--accent-hover)]',
  secondary:
    'border border-[var(--border-default)] bg-[var(--bg-surface-raised)] text-[var(--text-primary)] hover:border-[var(--border-strong)] hover:bg-[var(--bg-surface-hover)]',
  danger:
    'border border-[var(--danger-border)] bg-[var(--danger-bg)] text-[var(--danger)] hover:border-[var(--danger)]',
  ghost:
    'text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)]',
  success:
    'border border-[var(--success-border)] bg-[var(--success-bg)] text-[var(--success)] hover:border-[var(--success)]',
};

const sizeClasses = {
  sm: 'min-h-8 px-2.5 py-1 text-xs gap-1.5',
  md: 'min-h-9 px-3 py-1.5 text-[13px] gap-2',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', icon, size = 'md', children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center rounded-md font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40',
        variantClasses[variant],
        sizeClasses[size],
        className,
      )}
      {...props}
    >
      {icon && <span className="shrink-0">{icon}</span>}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
