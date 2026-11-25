import { Scissors } from 'lucide-react'
import { useState } from 'react'
import type { Task } from '../../types'
import type { MultiCutBasketController } from '../production/useMultiCutBasket'
import { TaskClipPicker } from './TaskClipPicker'
import { TaskQuickPreview } from './TaskQuickPreview'
import { taskPlayableMedia } from './taskCenterMedia'

/**
 * Own both picker and picker-launched player outside the task card's render
 * state. Opening this control does not re-render task status or the cover.
 * A player is stacked above the still-open picker, so closing it restores
 * clip selection rather than jumping back to the task center.
 */
export function TaskClipPickerLauncher({ task, basket }: {
  task: Task
  basket: MultiCutBasketController
}) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const [previewKey, setPreviewKey] = useState<string | null>(null)
  const selectedCount = basket.enabled
    ? task.variants.filter(item => item.downloadArtifactId && basket.isSelected(item.id)).length
    : 0
  const media = previewKey ? taskPlayableMedia(task,
    basket.relatedMultiSourceTasksForTask(task.id).concat(
      task.variants.flatMap(variant => basket.singleSourceVersionsFor(variant.id)),
    )) : []

  const closePicker = () => {
    setPreviewKey(null)
    setPickerOpen(false)
  }

  return <>
    <button type="button" className="task-center-card__pick" onClick={() => setPickerOpen(true)}
      aria-label={'选择原片送剪：' + task.title} title="选择原片送剪">
      <Scissors size={15} aria-hidden="true" />
      <span>选片{selectedCount ? ' · ' + selectedCount : ''}</span>
    </button>
    {pickerOpen ? <TaskClipPicker task={task} basket={basket} suspended={previewKey !== null}
      onClose={closePicker} onPreview={setPreviewKey} /> : null}
    {previewKey ? <TaskQuickPreview media={media} startKey={previewKey}
      onClose={() => setPreviewKey(null)} onViewed={basket.refreshMultiTasks} /> : null}
  </>
}
