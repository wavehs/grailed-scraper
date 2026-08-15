import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
export const Button = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement>>(
  ({ className, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 hover:bg-slate-700',
        className,
      )}
      {...props}
    />
  ),
);
Button.displayName = 'Button';
