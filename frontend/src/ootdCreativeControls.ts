export type VideoDisplayFocus = 'overall' | 'print' | 'material' | 'fit' | 'back'

export const VIDEO_DISPLAY_FOCUSES: { value: VideoDisplayFocus; label: string }[] = [
  { value: 'overall', label: '整体穿搭' },
  { value: 'print', label: '印花与文字' },
  { value: 'material', label: '材质与细节' },
  { value: 'fit', label: '版型与垂坠' },
  { value: 'back', label: '背面设计（有参考时）' },
]

export function focusUsesProductImages(focus: VideoDisplayFocus): boolean {
  return focus === 'print' || focus === 'material' || focus === 'back'
}
