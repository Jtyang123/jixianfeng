export function approvalPresentation(deliveryStatus, approved) {
  if (deliveryStatus === 'ready' && !approved) return 'pending'
  if (approved) return 'approved'
  return 'review'
}

export function scoreDeltaLabel(value) {
  const numeric = Number(value || 0)
  return `${numeric >= 0 ? '+' : ''}${numeric}`
}
