import './uploadStatus.css'
export type FileUploadState = {
  phase: 'preview' | 'uploading' | 'validated' | 'failed' | 'cancelled'
  message?: string
}
const labels: Record<FileUploadState['phase'], string> = {
  preview: '本地预览 · 待后台校验',
  uploading: '正在上传并校验',
  validated: '已上传 · 后台校验通过',
  failed: '上传未完成 · 查看错误原因',
  cancelled: '已中止 · 尚未通过校验',
}
export function UploadStatus({ state }: { state: FileUploadState }) {
  return <small className={'upload-validation-status upload-validation-status--' + state.phase}
    role="status" title={state.message || labels[state.phase]}>{labels[state.phase]}</small>
}
