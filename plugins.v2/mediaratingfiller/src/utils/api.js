export function unwrapResponse(response) {
  if (response == null) {
    return null
  }
  if (typeof response === 'object' && Object.prototype.hasOwnProperty.call(response, 'success')) {
    if (!response.success) {
      throw new Error(response.message || '请求失败')
    }
    return response.data ?? null
  }
  return response
}
