import { Check, LoaderCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { Task } from '../../types'
import { taskStatus } from '../../domain/taskPresentation'
import { deepVideoStageLabel, videoModelDisplayName } from '../../providerPresentation'
import { stageElapsedSeconds, taskTimingStages } from '../../domain/taskTimingPresentation'
import { formatDurationSeconds } from '../../taskTiming'
import { TaskElapsed } from '../../shared/components/TaskTiming'

/** Compact, receipt-backed production progress: never invents a completion percentage or ETA. */
export function TaskAdaptiveProgress({ task }: { task: Task }) {
  const [now, setNow] = useState(Date.now)
  const stages = taskTimingStages(task)
  const activeTiming = stages.find(stage => stage.status === 'ACTIVE')
  const hasActiveTiming = Boolean(activeTiming)
  useEffect(() => {
    if (!hasActiveTiming) return
    const interval = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(interval)
  }, [hasActiveTiming])

  const ready = task.variants.filter(variant => Boolean(variant.downloadArtifactId)).length
  const total = task.variants.length
  const isDeepVideo = task.workflowKind === 'DEEP_VIDEO'
  const deepVideoModel = task.settings?.model ?? task.compiledPlan?.model
  const deepVideoDepthModel = task.settings?.depthModel
  const isDepthOnly = isDeepVideo && task.settings?.deepVideoMode === 'depth'
  const isDepthFirst = isDeepVideo && task.settings?.deepVideoPipeline === 'depth-first-v1'
  const isPreparing = ['DRAFT', 'ANALYZING', 'PREPARING', 'WAITING_FOR_SEAL'].includes(task.phase)
  const currentStep = isPreparing ? 0 : isDepthFirst && task.settings?.deepVideoStage === 'replacement' ? 2 : 1
  const labels = isDeepVideo
    ? isDepthOnly
      ? ['准备素材', deepVideoStageLabel('DEPTH_EXTRACTION', deepVideoModel, deepVideoDepthModel), '结果下载']
      : isDepthFirst
        ? ['准备素材', deepVideoStageLabel('DEPTH_EXTRACTION', deepVideoModel, deepVideoDepthModel), deepVideoStageLabel('REPLACEMENT', deepVideoModel), '剪辑交付']
        : ['准备素材', (videoModelDisplayName(deepVideoModel) ?? '视频') + '替换', '剪辑交付']
    : ['素材与方案', '生成原片', '剪辑交付']
  const activeSeconds = activeTiming ? stageElapsedSeconds(activeTiming, now) : null
  return <section className="task-adaptive-progress" aria-label="当前制作进度">
    <div className="task-adaptive-progress__current">
      <LoaderCircle size={20} className="spin" aria-hidden="true" />
      <div className="task-adaptive-progress__copy">
        <h2 role="status">{taskStatus(task).label}</h2>
        <p>{total ? `原片已就绪 ${ready}/${total}` : '等待生成结果'}
          <span aria-hidden="true"> · </span>
          {activeSeconds !== null
            ? <>本阶段已用 {formatDurationSeconds(activeSeconds)}</>
            : <>任务累计用时 <TaskElapsed task={task} /></>}
        </p>
      </div>
    </div>
    <ol className="task-adaptive-progress__steps" aria-label="制作阶段">
      {labels.map((label, index) => <li key={label} className={index < currentStep ? 'is-complete' : index === currentStep ? 'is-current' : 'is-pending'} aria-current={index === currentStep ? 'step' : undefined}>
        <span className="task-adaptive-progress__marker">{index < currentStep ? <Check size={13} /> : index + 1}</span>
        <span>{label}</span>
      </li>)}
    </ol>
  </section>
}
