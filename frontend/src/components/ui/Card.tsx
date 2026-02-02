'use client'

import { forwardRef, HTMLAttributes } from 'react'
import { motion, HTMLMotionProps } from 'framer-motion'
import clsx from 'clsx'

interface CardProps extends Omit<HTMLMotionProps<'div'>, 'ref'> {
  variant?: 'default' | 'elevated' | 'glass'
  hover?: boolean
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant = 'default', hover = false, children, ...props }, ref) => {
    const variants = {
      default: 'bg-surface border border-border',
      elevated: 'bg-surface-elevated border border-border shadow-xl shadow-black/20',
      glass: 'glass border border-border/50',
    }

    return (
      <motion.div
        ref={ref}
        className={clsx(
          'rounded-2xl p-6',
          variants[variant],
          hover && 'transition-all duration-300 hover:border-border-light hover:shadow-lg hover:shadow-black/30',
          className
        )}
        whileHover={hover ? { y: -2 } : undefined}
        {...props}
      >
        {children}
      </motion.div>
    )
  }
)

Card.displayName = 'Card'

export const CardHeader = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={clsx('mb-4', className)} {...props} />
)

export const CardTitle = ({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) => (
  <h3 className={clsx('text-lg font-semibold text-text-primary', className)} {...props} />
)

export const CardDescription = ({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) => (
  <p className={clsx('text-sm text-text-secondary mt-1', className)} {...props} />
)

export const CardContent = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={clsx('', className)} {...props} />
)
