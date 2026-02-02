'use client'

import { forwardRef, InputHTMLAttributes } from 'react'
import clsx from 'clsx'

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  icon?: React.ReactNode
  error?: string
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, icon, error, ...props }, ref) => {
    return (
      <div className="relative w-full">
        {icon && (
          <div className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted">
            {icon}
          </div>
        )}
        <input
          ref={ref}
          className={clsx(
            'w-full bg-surface-elevated border rounded-xl px-4 py-3 text-text-primary placeholder:text-text-muted',
            'transition-all duration-200',
            'focus:outline-none focus:ring-2 focus:ring-accent/50 focus:border-accent',
            'hover:border-border-light',
            icon && 'pl-12',
            error ? 'border-error' : 'border-border',
            className
          )}
          {...props}
        />
        {error && (
          <p className="mt-1.5 text-sm text-error">{error}</p>
        )}
      </div>
    )
  }
)

Input.displayName = 'Input'
