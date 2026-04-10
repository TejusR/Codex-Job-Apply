import type { ReactNode } from "react";

interface DrawerProps {
  isOpen: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

export function Drawer({ isOpen, title, onClose, children }: DrawerProps) {
  return (
    <div className={isOpen ? "drawer drawer--open" : "drawer"} aria-hidden={!isOpen}>
      <button
        aria-label="Close details"
        className="drawer__scrim"
        onClick={onClose}
        type="button"
      />
      <aside className="drawer__panel">
        <div className="drawer__header">
          <div>
            <p className="eyebrow">Job Detail</p>
            <h2>{title}</h2>
          </div>
          <button className="ghost-button" onClick={onClose} type="button">
            Close
          </button>
        </div>
        <div className="drawer__content">{children}</div>
      </aside>
    </div>
  );
}
