"use client";

import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  type ReactNode,
} from "react";
import { cn } from "@/lib/utils";

interface SlideOverProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Width in px; default 480 */
  width?: number;
  className?: string;
  hideClose?: boolean;
}

export function SlideOver({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = 480,
  className,
  hideClose,
}: SlideOverProps) {
  const titleId = useId();
  const descId = useId();
  const panelRef = useRef<HTMLDivElement>(null);

  // ESC to close
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Initial focus
  useEffect(() => {
    if (!open) return;
    const node = panelRef.current;
    if (!node) return;
    const focusable = node.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    focusable?.focus();
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-40" role="presentation">
          <motion.div
            className="absolute inset-0 bg-zinc-950/60 backdrop-blur-[2px]"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            onClick={onClose}
            aria-hidden
          />
          <motion.aside
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby={title ? titleId : undefined}
            aria-describedby={description ? descId : undefined}
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            className={cn(
              "absolute right-0 top-0 flex h-full flex-col border-l border-zinc-800 bg-zinc-900 shadow-[0_8px_24px_rgba(0,0,0,0.5)]",
              className
            )}
            style={{ width }}
          >
            {(title || !hideClose) && (
              <div className="flex items-start justify-between gap-4 border-b border-zinc-800 px-5 py-4">
                <div className="min-w-0">
                  {title && (
                    <h2
                      id={titleId}
                      className="truncate text-base font-semibold text-zinc-50"
                    >
                      {title}
                    </h2>
                  )}
                  {description && (
                    <p id={descId} className="mt-1 text-xs text-zinc-400">
                      {description}
                    </p>
                  )}
                </div>
                {!hideClose && (
                  <button
                    type="button"
                    aria-label="닫기"
                    onClick={onClose}
                    className="grid h-8 w-8 place-items-center rounded-md text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            )}
            <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
            {footer && (
              <div className="flex items-center justify-end gap-2 border-t border-zinc-800 bg-zinc-950/40 px-5 py-3">
                {footer}
              </div>
            )}
          </motion.aside>
        </div>
      )}
    </AnimatePresence>
  );
}
