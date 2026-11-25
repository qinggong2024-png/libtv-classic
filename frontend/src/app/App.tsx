// 应用入口（2026-09-21 前端架构拆分 v1）。
// 只负责：Provider 组合 + 导航 + 视图分发 + 认证边界。
// 不含 API 调用、不含轮询/通知/生产流程实现（分别在
// features/production/hooks 与 app/use*.ts）。
//
// ★ 代码分割说明（勿改动语义）：
//   admin / settings / library 用 React.lazy 做页面级分割，仅当对应 view 被打开时才拉取。
//   ImageCenter 也走了 dynamic import，获授权后必须持续挂载 —— 它靠在非激活时
//   继续轮询来推送图片任务完成通知（onNotifications / onActiveTasksChange）。
//   因此它被包在 <Suspense fallback={null}> 里，只由图片页面权限控制挂载；
//   一旦改成 {view === 'images' ? <ImageCenter/> : null}，非激活期轮询与通知会直接丢失。
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Bell, Images, Library, ListVideo, LoaderCircle, Plus, RefreshCw, Settings, ShieldCheck, X } from 'lucide-react'
import { cancelTask, logout } from '../api'
import { useTaskAttentionAcknowledgements } from './useTaskAttentionAcknowledgements'
import { ACTIVE_PHASES } from '../domain/workflowStates'
import { isTaskAttentionAcknowledged, taskAttentionGroup, taskAttentionSignature } from '../domain/taskAttention'
import { LoginScreen } from '../features/auth/LoginScreen'
import { ImageVideoComposerSheet } from '../features/production/ImageVideoComposerSheet'
import { SharedCutflowWorkspace } from '../features/production/SharedCutflowWorkspace'
import { useProductionActions } from '../features/production/hooks/useProductionActions'
import { useTaskPolling } from '../features/production/hooks/useTaskPolling'
import { RecordsView } from '../features/task-center/RecordsView'
import { writeStorage } from '../safeStorage'
import { InPageTaskToasts } from '../shared/components/InPageTaskToasts'
import { VideoPreview } from '../shared/components/Media'
import { AppShell } from './AppShell'
import type { NavItem } from './AppShell'
import { useAppNavigation } from './useAppNavigation'
import { useTaskNotifications } from './useTaskNotifications'
import type { View } from './appTypes'
import { hasPage, isManagementAdmin, permittedView } from '../pageAccess'
import { GenerationBudgetProvider } from '../shared/components/GenerationBudgetProvider'

// 页面级 lazy：只在进入对应 view 时才下载该页面 chunk。
const ImageCenter = lazy(() => import('../features/image-center/ImageCenter').then((m) => ({ default: m.ImageCenter })))
const ContentLibraryView = lazy(() => import('../features/library/ContentLibraryView').then((m) => ({ default: m.ContentLibraryView })))
const AdminCenterView = lazy(() => import('../features/admin/AdminCenterView').then((m) => ({ default: m.AdminCenterView })))
const SettingsView = lazy(() => import('../features/settings/SettingsView').then((m) => ({ default: m.SettingsView })))
const HomeView = lazy(() => import('../features/task-center/HomeView').then((m) => ({ default: m.HomeView })))
const ProductionWorkspace = lazy(() => import('../features/production/ProductionWorkspace').then((m) => ({ default: m.ProductionWorkspace })))

function PageLoading() {
  return <div className="page-loading"><LoaderCircle className="spin" size={26} /><strong>正在加载页面</strong></div>
}

function App() {
  const { view: requestedView, detailId, focusVariantId, adminSection, recordsFilter, recordsQuery, recordsRestoreScroll, setRecordsRestoreScroll, navigate, navigateRecords, openTaskFromRecords, closeTaskDetail } = useAppNavigation()
  const [sidebarInfo, setSidebarInfo] = useState<'account' | null>(null)
  const [hasActiveImageTasks, setHasActiveImageTasks] = useState(false)
  const [imageNotificationTaskId, setImageNotificationTaskId] = useState<string | null>(null)

  const {
    tasks, setTasks,
    system, actor, setActor,
    devActorRole, setDevActorRole,
    loading, setLoading,
    authRequired, setAuthRequired,
    error, setError,
    lastSyncedAt,
    nextTaskCursor, setNextTaskCursor,
    loadingMoreTasks, setLoadingMoreTasks,
    refreshTasks, refresh,
  } = useTaskPolling(detailId)
  const view = permittedView(actor, requestedView, detailId)
  const canMake = hasPage(actor, 'make')
  const canImages = hasPage(actor, 'images')
  const canRecords = hasPage(actor, 'records')


  const {
    browserNotificationsEnabled,
    notificationPermission,
    notificationPromptDismissed,
    setNotificationPromptDismissed,
    inPageTaskToasts,
    setInPageTaskToasts,
    enableBrowserNotifications,
    disableBrowserNotifications,
    handleImageNotifications,
  } = useTaskNotifications({ tasks, actor, detailId, view, navigate, setImageNotificationTaskId })

  const {
    video, setVideo,
    seed,
    imageComposerSeed,
    remixStage,
    taskChanged,
    loadMoreTasks,
    remix,
    reuseCompleted,
    prepareDepthVideo,
    prepareImageCenterVideos,
    play,
    created,
    imageComposerCreated,
    closeImageComposer,
    feedbackChanged,
  } = useProductionActions({
    detailId,
    nextTaskCursor,
    loadingMoreTasks,
    setLoadingMoreTasks,
    setNextTaskCursor,
    setTasks,
    refreshTasks,
    setError,
    navigate,
  })

  const switchView = useCallback((next: View) => {
    navigate(next)
    window.scrollTo({ top: 0, behavior: 'auto' })
  }, [navigate])

  useEffect(() => {
    if (view !== 'records' || recordsRestoreScroll === null) return
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: recordsRestoreScroll, behavior: 'auto' })
      setRecordsRestoreScroll(null)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [recordsRestoreScroll, setRecordsRestoreScroll, view])

  const switchDevelopmentActor = useCallback(() => {
    const next = devActorRole === 'admin' ? 'user' : 'admin'
    writeStorage('local', 'development-actor-role', next)
    setDevActorRole(next)
    setSidebarInfo(null)
    setLoading(true)
  }, [devActorRole, setDevActorRole, setLoading])

  const signOut = useCallback(async () => {
    try { await logout() } catch { /* 本地状态仍会回到登录边界。 */ }
    setActor(null)
    setTasks([])
    setSidebarInfo(null)
    setAuthRequired(true)
  }, [setActor, setAuthRequired, setTasks])

  const { acknowledgements, acknowledge, restore, error: attentionError } = useTaskAttentionAcknowledgements(actor?.userId ?? null)
  const selectedTask = tasks.find((task) => task.id === detailId) ?? null
  const showNotificationPrompt = browserNotificationsEnabled
    && notificationPermission === 'default'
    && !notificationPromptDismissed
    && (hasActiveImageTasks || tasks.some((task) => ACTIVE_PHASES.has(task.phase) || task.phase === 'AWAITING_CONFIRMATION'))

  if (loading && !actor && !authRequired) {
    return <main className="app-boot-screen"><div role="status" aria-label="正在连接生产服务"><LoaderCircle className="spin" size={22} /></div></main>
  }

  if (system?.sharedAuthentication && authRequired) {
    return <LoginScreen onAuthenticated={async () => { setLoading(true); await refresh() }} />
  }

  const canOperate = isManagementAdmin(actor)
  const nav: NavItem[] = [
    ...(canImages ? [{ value: 'images' as const, label: '图片中心', icon: <Images size={18} /> }] : []),
    ...(canMake ? [{ value: 'make' as const, label: '视频制作', icon: <Plus size={18} /> }] : []),
    ...(canRecords ? [{ value: 'records' as const, label: '任务', icon: <ListVideo size={18} />, badge: 0, badgeLabel: '待处理任务' }] : []),
    ...(hasPage(actor, 'library') ? [{ value: 'library' as const, label: '内容库', icon: <Library size={18} /> }] : []),
    ...(canOperate ? [{ value: 'admin' as const, label: '管理', icon: <ShieldCheck size={18} /> }] : []),
    { value: 'settings', label: '设置', icon: <Settings size={18} /> },
  ]
  const actorId = system?.sharedAuthentication ? null : actor?.userId ?? null
  // 仅在当前账号的任务页和详情页共享选片篮，避免跨账号串片。
  const basketStorageKey = `cutflow-multi-source-selection:${actor?.userId ?? 'anonymous'}`

  const overlays = <>
    <InPageTaskToasts
      toasts={inPageTaskToasts}
      onOpen={(toast) => {
        setInPageTaskToasts((current) => current.filter((item) => item.id !== toast.id))
        if (toast.view === 'images') setImageNotificationTaskId(toast.taskId)
        navigate(toast.view, toast.view === 'make' ? toast.taskId : null)
      }}
      onDismiss={(id) => setInPageTaskToasts((current) => current.filter((toast) => toast.id !== id))}
    />
    {video ? <VideoPreview artifactId={video.artifactId} title={video.title} onClose={() => setVideo(null)} /> : null}
    {imageComposerSeed && canMake ? <ImageVideoComposerSheet seed={imageComposerSeed} system={system} actorId={actorId} onClose={closeImageComposer} onCreated={imageComposerCreated} /> : null}
    {remixStage ? <div className="global-loading"><LoaderCircle className="spin" size={20} /><span>{remixStage === 'reuse' ? '正在复用素材和提示词并提交生成' : '正在加载原素材'}</span></div> : null}
  </>

  return <GenerationBudgetProvider key={actor?.userId ?? 'anonymous'}>
    <SharedCutflowWorkspace key={actor?.userId ?? 'anonymous'} tasks={tasks}
      enabled={Boolean(system?.cutflowMultiSourceReady) && (canMake || canRecords)}
      canWrite={actor?.role !== 'AUDITOR'}
      storageKey={basketStorageKey}
      visible={!video && !imageComposerSeed && (view === 'records' || (view === 'make' && Boolean(selectedTask)))}
      scopeKey={`${view}:${selectedTask?.id ?? ''}`}
      onSubmitted={() => void refreshTasks()}
      onPlay={play}
    >{(sharedBasket) => {
      const attentionStates = tasks.map(task => {
        const batches = sharedBasket.relatedMultiSourceTasksForTask(task.id)
          .concat(task.variants.flatMap(variant => sharedBasket.singleSourceVersionsFor(variant.id)))
        const load = {
          loaded: !sharedBasket.enabled || sharedBasket.multiTasksLoaded,
          error: Boolean(sharedBasket.multiTaskError),
          now: sharedBasket.multiTasksCheckedAt,
        }
        const signature = taskAttentionSignature(task, batches)
        return {
          task,
          signature,
          group: taskAttentionGroup(task, batches, load),
          acknowledged: isTaskAttentionAcknowledged(task.id, signature, acknowledgements),
        }
      })
      const pendingAttention = attentionStates.filter(item => item.group === 'attention' && !item.acknowledged)
      const taskNav = nav.map(item => item.value === 'records'
        ? { ...item, badge: pendingAttention.length, badgeLabel: `待处理任务 ${pendingAttention.length} 个` }
        : item)
      return <AppShell
    nav={taskNav}
    view={view}
    onSwitchView={switchView}
    actor={actor}
    system={system}
    error={error}
    sidebarInfo={sidebarInfo}
    onSidebarInfoChange={setSidebarInfo}
    devActorRole={devActorRole}
    onSwitchDevelopmentActor={switchDevelopmentActor}
    onSignOut={() => void signOut()}
    errorResetKeys={[view, adminSection, selectedTask?.id]}
    onErrorBack={() => navigate(permittedView(actor, 'make', null))}
    overlays={overlays}
  >
    {loading ? <PageLoading /> : <>
      {error ? <div className="service-banner"><AlertTriangle size={18} /><span>{error}</span><button onClick={() => void refresh()}><RefreshCw size={15} />重新连接</button></div> : null}
      {showNotificationPrompt ? <div className="notification-permission-banner" role="region" aria-label="浏览器通知">
        <Bell size={18} />
        <span><strong>后台进度提醒</strong><small>开启通知，图片或视频完成、异常以及方案待确认时会及时提醒你。</small></span>
        <button className="secondary-button" type="button" onClick={() => void enableBrowserNotifications()}>开启通知</button>
        <button className="icon-button" type="button" aria-label="暂不开启通知" title="暂不开启" onClick={() => setNotificationPromptDismissed(true)}><X size={17} /></button>
      </div> : null}
      {canImages ? <Suspense fallback={null}>
        <ImageCenter
          active={view === 'images'}
          draftScope={actor?.userId ?? null}
          openTaskId={imageNotificationTaskId}
          onPrepareVideos={prepareImageCenterVideos}
          canMakeVideos={canMake}
          onNotifications={handleImageNotifications}
          onActiveTasksChange={setHasActiveImageTasks}
        />
      </Suspense> : null}
      {/* make 是默认落地视图。它同时走 lazy 是为了把 entry chunk 压到 300 kB 以下
          （方案验收线）。代价：应用打开时多一次页面级加载（此前已有 boot screen，
          局域网内可忽略）。若不接受这次可见变化，把 HomeView / ProductionWorkspace
          改回顶部静态 import 并删掉本 Suspense 即可，entry 会回到约 333 kB。 */}
      <Suspense fallback={view === 'make' ? <PageLoading /> : null}>
        {canMake ? <HomeView imageDraftScope={actor?.userId ?? null} active={view === 'make' && !selectedTask} seed={seed} system={system} actorId={actorId} onCreated={created} /> : null}
        {view === 'make' && selectedTask ? <ProductionWorkspace onFeedbackChanged={actor?.role !== 'AUDITOR' ? feedbackChanged : undefined} canGenerate={canMake} tasks={tasks} basketStorageKey={basketStorageKey} sharedBasket={sharedBasket} key={selectedTask.id} task={selectedTask} focusVariantId={focusVariantId} multiCutReady={Boolean(system?.cutflowMultiSourceReady)} onClose={closeTaskDetail} onChanged={taskChanged} onDepthReuse={() => void prepareDepthVideo(selectedTask)} onRemix={() => void remix(selectedTask)} onReuse={(variants) => void reuseCompleted(selectedTask, variants)} onPlay={(variant) => play(selectedTask, variant)} onPlaySource={play} /> : null}
      </Suspense>
      {view === 'records' ? <RecordsView sharedBasket={sharedBasket}
          attentionAcknowledgements={acknowledgements}
          onDismissAttention={(task, signature) => { void acknowledge(task.id, signature) }}
          onRestoreAttention={(task) => { void restore(task.id) }}
          onCancelTask={async task => { await cancelTask(task.id); await refreshTasks() }}
          attentionError={attentionError} canGenerate={canMake} storageKey={basketStorageKey} tasks={tasks} hasMore={Boolean(nextTaskCursor)} loadingMore={loadingMoreTasks} lastSyncedAt={lastSyncedAt} syncError={Boolean(error)} onLoadMore={() => void loadMoreTasks()} onRefresh={refreshTasks} onOpen={(task) => openTaskFromRecords(task.id)} onPlay={play} onRemix={(task) => void remix(task)} onFeedbackChanged={feedbackChanged} onCreateTask={() => navigate('make')} onEditing={(task, variant) => openTaskFromRecords(task.id, variant.id)} canReview={actor?.role === 'ADMIN' || actor?.role === 'OPERATOR'} canWrite={actor?.role !== 'AUDITOR'} initialFilter={recordsFilter} initialQuery={recordsQuery} onStateChange={navigateRecords} multiCutReady={Boolean(system?.cutflowMultiSourceReady)} /> : null}
      {view === 'library' && isManagementAdmin(actor) ? <Suspense fallback={<PageLoading />}><ContentLibraryView
        actorId={actorId}
        canDeleteSubjects={isManagementAdmin(actor)}
        canEditCatalog={isManagementAdmin(actor)}
      /></Suspense> : null}
      {view === 'admin' && canOperate ? <Suspense fallback={<PageLoading />}><AdminCenterView system={system} actor={actor} section={adminSection} onSectionChange={(section) => navigate('admin', null, false, section)} onShowTasks={() => navigateRecords('attention', '')} onKlingChanged={refresh} /></Suspense> : null}
      {view === 'settings' ? <Suspense fallback={<PageLoading />}><SettingsView
        system={system}
        actorId={actor?.userId}
        onLogout={() => void signOut()}
        browserNotificationsEnabled={browserNotificationsEnabled}
        browserNotificationPermission={notificationPermission}
        onEnableBrowserNotifications={enableBrowserNotifications}
        onDisableBrowserNotifications={disableBrowserNotifications}
      /></Suspense> : null}
    </>}
  </AppShell> }}</SharedCutflowWorkspace></GenerationBudgetProvider>
}

export default App
