interface EmptyStateProps {
  title: string;
  body: string;
}

export function EmptyState({ title, body }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <p className="eyebrow">No Data Yet</p>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}
