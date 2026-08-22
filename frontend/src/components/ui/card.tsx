import { type HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'glass rounded-xl transition-all duration-200 hover:border-[rgba(255,255,255,0.1)] animate-fade-in',
        className,
      )}
      {...props}
    />
  );
}
