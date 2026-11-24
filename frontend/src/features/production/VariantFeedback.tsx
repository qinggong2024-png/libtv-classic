import { LoaderCircle, ThumbsDown, ThumbsUp } from 'lucide-react'
import { useRef, useState } from 'react'
import { setVariantFeedback } from '../../api'
import { errorMessage } from '../../errorPresentation'
import type { FeedbackValue, Task, Variant } from '../../types'
import { GenerationFeedbackPanel } from '../../GenerationFeedback'

export function VariantFeedback({ task, variant, onFeedbackChanged }: { task: Task; variant: Variant; onFeedbackChanged: (task: Task) => void }) {
  const [feedbackBusy, setFeedbackBusy] = useState<FeedbackValue | null>(null)
  const [feedbackMessage, setFeedbackMessage] = useState('')
  const [animatedFeedback, setAnimatedFeedback] = useState<FeedbackValue | null>(null)
  const [feedbackPanel, setFeedbackPanel] = useState<FeedbackValue | null>(null)
  const feedbackAnchorRef = useRef<HTMLDivElement>(null)
  const submitFeedback = async (value: FeedbackValue) => {
    const nextValue = variant.feedback === value ? null : value
    setFeedbackBusy(value)
    setFeedbackMessage('')
    try {
      const changed = await setVariantFeedback(task.id, variant.id, nextValue)
      onFeedbackChanged(changed)
      setAnimatedFeedback(nextValue)
      setFeedbackPanel(nextValue)
      setFeedbackMessage(nextValue ? '已记录，可选择具体原因' : '已取消反馈')
      window.setTimeout(() => setAnimatedFeedback(null), 320)
    } catch (cause) {
      setFeedbackMessage(errorMessage(cause, '反馈提交失败，请重试'))
    } finally {
      setFeedbackBusy(null)
    }
  }
  const saveFeedbackDetails = async (reasons: Variant['feedbackReasons'], comment: string | null) => {
    if (!feedbackPanel) return false
    setFeedbackBusy(feedbackPanel)
    setFeedbackMessage('')
    try {
      const changed = await setVariantFeedback(task.id, variant.id, feedbackPanel, { reasons, comment })
      onFeedbackChanged(changed)
      setFeedbackMessage('详细反馈已保存，将优化后续同类场景')
      return true
    } catch (cause) {
      setFeedbackMessage(errorMessage(cause, '详细反馈保存失败，请重试'))
      return false
    } finally {
      setFeedbackBusy(null)
    }
  }
  return <div ref={feedbackAnchorRef} className="result-feedback">
      <span className="feedback-copy" aria-live="polite">{feedbackMessage || (variant.feedback ? '感谢反馈，系统会持续学习' : '这个结果怎么样？')}</span>
      <span className="feedback-buttons">
        <button className={`feedback-button${variant.feedback === 'LIKE' ? ' is-selected is-positive' : ''}${animatedFeedback === 'LIKE' ? ' is-animating' : ''}`} disabled={feedbackBusy !== null} onClick={() => void submitFeedback('LIKE')} aria-label={variant.feedback === 'LIKE' ? '取消点赞' : '点赞此视频'} aria-pressed={variant.feedback === 'LIKE'} title="点赞"><span>{feedbackBusy === 'LIKE' ? <LoaderCircle className="spin" size={17} /> : <ThumbsUp size={18} />}</span></button>
        <button className={`feedback-button${variant.feedback === 'DISLIKE' ? ' is-selected is-negative' : ''}${animatedFeedback === 'DISLIKE' ? ' is-animating' : ''}`} disabled={feedbackBusy !== null} onClick={() => void submitFeedback('DISLIKE')} aria-label={variant.feedback === 'DISLIKE' ? '取消点踩' : '点踩此视频'} aria-pressed={variant.feedback === 'DISLIKE'} title="点踩"><span>{feedbackBusy === 'DISLIKE' ? <LoaderCircle className="spin" size={17} /> : <ThumbsDown size={18} />}</span></button>
      </span>
      {feedbackPanel ? <GenerationFeedbackPanel
        key={`${variant.id}-${feedbackPanel}`}
        value={feedbackPanel}
        initialReasons={variant.feedback === feedbackPanel ? (variant.feedbackReasons ?? []) : []}
        initialComment={variant.feedback === feedbackPanel ? variant.feedbackComment : null}
        anchorRef={feedbackAnchorRef}
        onClose={() => setFeedbackPanel(null)}
        onSave={saveFeedbackDetails}
      /> : null}
    </div>
}
