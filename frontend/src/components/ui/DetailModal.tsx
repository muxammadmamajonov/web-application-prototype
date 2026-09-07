import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Pencil, Trash2 } from 'lucide-react';
import { Modal } from './Modal';
import { Button } from './Button';

/**
 * The ONE detail popup used across every list page. Layout is always identical:
 *   • Close (X)            → top-right (provided by Modal)
 *   • context-aware actions → bottom-right footer
 *
 * The footer is "context-aware" (per product decision): pass `onEdit`/`onDelete`
 * for normal CRUD entities, or `actions` for entity-specific verbs
 * (approve/reject, remind, close-contract). `readOnly` entities (audit, referrals)
 * pass neither and get just the Close button.
 *
 * Footer order, all right-aligned: [custom actions] [Delete] [Edit].
 */
interface DetailModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  size?: 'sm' | 'md' | 'lg';
  children: ReactNode;
  /** Extra entity-specific actions (approve/reject/remind…), shown left of Delete/Edit. */
  actions?: ReactNode;
  onEdit?: () => void;
  onDelete?: () => void;
  editLabel?: string;
  deleteLabel?: string;
  deleting?: boolean;
}

export function DetailModal({
  open, onClose, title, subtitle, size = 'md', children,
  actions, onEdit, onDelete, editLabel, deleteLabel, deleting,
}: DetailModalProps) {
  const { t } = useTranslation();
  const hasFooter = !!(actions || onEdit || onDelete);
  const footer = hasFooter ? (
    <>
      {actions}
      {onDelete && (
        <Button variant="outline" size="md" onClick={onDelete} loading={deleting}
                className="border-danger/40 text-danger hover:bg-[color:var(--c-danger-soft)]">
          <Trash2 size={16} /> {deleteLabel ?? t('common.delete', "O'chirish")}
        </Button>
      )}
      {onEdit && (
        <Button variant="inverse" size="md" onClick={onEdit}>
          <Pencil size={16} /> {editLabel ?? t('common.edit', 'Tahrirlash')}
        </Button>
      )}
    </>
  ) : undefined;

  return (
    <Modal open={open} onClose={onClose} title={title} subtitle={subtitle} size={size} footer={footer}>
      {children}
    </Modal>
  );
}

/** A single label → value row for the detail body. Hidden entirely when value is empty. */
export function DefRow({ label, children, hideEmpty = true }: {
  label: ReactNode; children: ReactNode; hideEmpty?: boolean;
}) {
  const empty = children == null || children === '' || children === false;
  if (hideEmpty && empty) return null;
  return (
    <div className="flex items-start justify-between gap-4 border-b border-line/60 py-2.5 last:border-0">
      <span className="shrink-0 text-body text-muted">{label}</span>
      <span className="min-w-0 break-words text-right text-body font-medium text-ink">{children}</span>
    </div>
  );
}

/** Wrapper for a set of DefRows. */
export function DefList({ children }: { children: ReactNode }) {
  return <div className="-my-1">{children}</div>;
}
