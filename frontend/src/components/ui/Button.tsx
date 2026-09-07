import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/cn';
import { buttonClasses, type Size, type Variant } from './buttonClasses';


interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  block?: boolean;
  /** Show a spinner and disable the button while an async action runs. */
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', block, loading, disabled, children, ...rest }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        buttonClasses({ variant, size, block }),
        'disabled:opacity-50 disabled:cursor-not-allowed',
        className,
      )}
      {...rest}
    >
      {loading && <Loader2 size={size === 'lg' ? 16 : 14} className="animate-spin" />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
