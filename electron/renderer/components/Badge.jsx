export default function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusBadge({ status }) {
  const normalized = String(status || 'unknown').toLowerCase();
  const tone = normalized.includes('verified') || normalized === 'active' || normalized === 'sent'
    ? 'success'
    : normalized.includes('fail') || normalized.includes('error') || normalized.includes('bounce')
      ? 'danger'
      : normalized.includes('queued') || normalized.includes('pending')
        ? 'warning'
        : 'neutral';
  return <Badge tone={tone}>{status || 'unknown'}</Badge>;
}

export function VerifiedBadge({ verified, label }) {
  return <Badge tone={verified ? 'success' : 'warning'}>{label || (verified ? 'verified' : 'unverified')}</Badge>;
}
