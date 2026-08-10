<template>
  <div class="tasks-page">
    <div class="page-title">任务管理</div>

    <el-row :gutter="16">
      <!-- 左侧：任务配置 -->
      <el-col :span="11">
        <el-card shadow="never" class="form-card">
          <!-- 三个 Tab -->
          <el-tabs v-model="activeTab" class="main-tabs">
            <!-- Tab 1：搜索用户 -->
            <el-tab-pane name="search_user">
              <template #label>
                <el-icon class="tab-icon"><User /></el-icon> 搜索用户
              </template>
              <el-alert title="按关键词搜索用户，找到匹配的达人账号" type="info" :closable="false" show-icon class="mb-3" />
              <el-form :model="searchForm" :rules="searchRules" ref="searchFormRef" label-width="110px">
                <el-form-item label="搜索关键词" prop="keywords">
                  <el-select v-model="searchForm.keywords" multiple filterable allow-create
                    placeholder="输入关键词后回车，可多个" style="width:100%" />
                </el-form-item>
                <el-divider content-position="left" class="div-sm">筛选条件</el-divider>
                <el-form-item label="粉丝下限">
                  <el-input-number v-model="searchForm.min_followers" :min="0" :step="10000"
                    controls-position="right" style="width:140px" />
                  <span class="unit-hint">0=不限，如 10000=1万</span>
                </el-form-item>
                <el-form-item label="总点赞下限">
                  <el-input-number v-model="searchForm.min_total_likes" :min="0" :step="1000"
                    controls-position="right" style="width:140px" />
                </el-form-item>
                <el-form-item label="必须有橱窗">
                  <el-switch v-model="searchForm.require_shop" />
                </el-form-item>
                <el-divider content-position="left" class="div-sm">联系方式</el-divider>
                <el-form-item label="联系方式">
                  <el-radio-group v-model="searchForm.contact_method">
                    <el-radio value="none">不联系</el-radio>
                    <el-radio value="dm"><el-icon><Message /></el-icon> 私信</el-radio>
                    <el-radio value="comment"><el-icon><ChatDotRound /></el-icon> 评论</el-radio>
                    <el-radio value="both">两种都发</el-radio>
                  </el-radio-group>
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="searchSubmitting" @click="submitSearch" size="large">
                    <el-icon><Promotion /></el-icon> 立即执行
                  </el-button>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- Tab 2：搜索作品 -->
            <el-tab-pane name="search_video">
              <template #label>
                <el-icon class="tab-icon"><VideoPlay /></el-icon> 搜索作品
              </template>
              <el-alert title="搜索相关视频作品，自动提取视频作者作为达人候选" type="info" :closable="false" show-icon class="mb-3" />
              <el-form :model="videoForm" :rules="searchRules" ref="videoFormRef" label-width="110px">
                <el-form-item label="搜索关键词" prop="keywords">
                  <el-select v-model="videoForm.keywords" multiple filterable allow-create
                    placeholder="如：美妆教程、好物分享" style="width:100%" />
                </el-form-item>
                <el-divider content-position="left" class="div-sm">筛选条件</el-divider>
                <el-form-item label="粉丝下限">
                  <el-input-number v-model="videoForm.min_followers" :min="0" :step="10000"
                    controls-position="right" style="width:140px" />
                  <span class="unit-hint">0=不限</span>
                </el-form-item>
                <el-form-item label="总点赞下限">
                  <el-input-number v-model="videoForm.min_total_likes" :min="0" :step="1000"
                    controls-position="right" style="width:140px" />
                </el-form-item>
                <el-divider content-position="left" class="div-sm">联系方式</el-divider>
                <el-form-item label="联系方式">
                  <el-radio-group v-model="videoForm.contact_method">
                    <el-radio value="none">不联系</el-radio>
                    <el-radio value="dm"><el-icon><Message /></el-icon> 私信</el-radio>
                    <el-radio value="comment"><el-icon><ChatDotRound /></el-icon> 评论</el-radio>
                    <el-radio value="both">两种都发</el-radio>
                  </el-radio-group>
                </el-form-item>
                <el-form-item>
                  <el-button type="primary" :loading="videoSubmitting" @click="submitVideo" size="large">
                    <el-icon><Promotion /></el-icon> 立即执行
                  </el-button>
                </el-form-item>
              </el-form>
            </el-tab-pane>

            <!-- Tab 3：直接联系 -->
            <el-tab-pane name="direct_contact">
              <template #label>
                <el-icon class="tab-icon"><Connection /></el-icon> 直接联系
              </template>
              <el-alert
                title="直接对指定用户发私信或评论最新作品，无需搜索流程"
                type="warning" :closable="false" show-icon class="mb-3"
              />

              <!-- 输入方式切换 -->
              <el-segmented v-model="inputMode" :options="inputModeOptions" class="mb-3" />

              <!-- 方式1：手动输入 -->
              <div v-if="inputMode === 'manual'">
                <el-form-item label-width="0">
                  <el-input
                    v-model="directForm.rawIds"
                    type="textarea"
                    :rows="7"
                    @input="onRawIdsChange"
                    placeholder="支持多种格式，每行一个或用逗号分隔：

• sec_user_id：MS4wLjABAAAAA0pSrRTe...
• 主页URL：https://www.douyin.com/user/MS4w...
• 抖音号：267581685（需点解析）
• 数字uid：84273394779（需点解析）"
                    style="font-family: monospace; font-size: 12px"
                  />
                </el-form-item>

                <!-- 格式统计 + 解析按钮 -->
                <div v-if="inputList.length > 0" class="format-stats">
                  <div class="format-tags">
                    <el-tag v-if="formatStats.sec_user_id" size="small" type="success" effect="plain">
                      sec_uid × {{ formatStats.sec_user_id }}
                    </el-tag>
                    <el-tag v-if="formatStats.url" size="small" type="primary" effect="plain">
                      URL × {{ formatStats.url }}
                    </el-tag>
                    <el-tag v-if="formatStats.short_id" size="small" type="warning" effect="plain">
                      抖音号/uid × {{ formatStats.short_id }}
                    </el-tag>
                    <el-tag v-if="formatStats.unknown" size="small" type="danger" effect="plain">
                      未知 × {{ formatStats.unknown }}
                    </el-tag>
                  </div>
                  <el-button
                    v-if="needsResolve"
                    size="small"
                    type="warning"
                    :loading="resolving"
                    :icon="Search"
                    @click="resolveIds"
                  >
                    解析抖音号/uid
                  </el-button>
                </div>
              </div>

              <!-- 方式2：上传文件 -->
              <div v-else>
                <el-upload
                  ref="uploadRef"
                  :auto-upload="false"
                  :on-change="handleFileChange"
                  :on-remove="handleFileRemove"
                  accept=".txt,.csv"
                  :limit="1"
                  drag
                  class="upload-area mb-2"
                >
                  <el-icon size="36"><UploadFilled /></el-icon>
                  <div class="el-upload__text">拖拽文件到此处，或 <em>点击上传</em></div>
                  <template #tip>
                    <div class="upload-tip">
                      支持 .txt / .csv，每行一个标识符（sec_user_id / 抖音号 / uid / 主页URL），CSV 取第一列，最多 500 条
                    </div>
                  </template>
                </el-upload>

                <!-- 解析按钮（文件上传后） -->
                <div v-if="parsedIds.length > 0" class="format-stats">
                  <div class="format-tags">
                    <el-tag size="small" type="info" effect="plain">共 {{ parsedIds.length }} 条</el-tag>
                    <el-tag v-if="needsResolve" size="small" type="warning" effect="plain">
                      含抖音号/uid，需解析
                    </el-tag>
                  </div>
                  <el-button
                    v-if="needsResolve"
                    size="small" type="warning" :loading="resolving" :icon="Search"
                    @click="resolveIds"
                  >
                    解析抖音号/uid
                  </el-button>
                </div>
              </div>

              <!-- 解析结果预览 -->
              <div v-if="resolvedItems.length > 0" class="resolve-result">
                <el-divider content-position="left" class="div-sm">解析结果</el-divider>
                <el-scrollbar max-height="160px">
                  <div v-for="item in resolvedItems" :key="item.input" class="resolve-row">
                    <el-icon :color="item.status === 'ok' ? '#52c41a' : '#ff4d4f'">
                      <component :is="item.status === 'ok' ? 'CircleCheck' : 'CircleClose'" />
                    </el-icon>
                    <span class="resolve-input">{{ item.input.slice(0, 20) }}</span>
                    <el-tag size="small" :type="item.status === 'ok' ? 'success' : 'danger'" effect="plain">
                      {{ item.status === 'ok' ? (item.nickname || item.sec_user_id.slice(0,12) + '...') : item.msg || '未找到' }}
                    </el-tag>
                  </div>
                </el-scrollbar>
              </div>

              <!-- 联系方式 -->
              <el-divider content-position="left" class="div-sm">联系方式</el-divider>
              <el-radio-group v-model="directForm.contact_method" class="mb-3">
                <el-radio value="dm">
                  <el-icon><Message /></el-icon> 私信
                </el-radio>
                <el-radio value="comment">
                  <el-icon><ChatDotRound /></el-icon> 评论最新作品
                </el-radio>
                <el-radio value="both">
                  两种都发
                </el-radio>
              </el-radio-group>

              <!-- 统计预览 -->
              <div class="id-count-bar" v-if="inputList.length > 0 || finalSecIds.length > 0">
                <el-icon><CircleCheck /></el-icon>
                <span v-if="needsResolve && resolvedItems.length === 0">
                  共 <strong>{{ inputList.length }}</strong> 条输入，含需解析格式，请先点「解析抖音号/uid」
                </span>
                <span v-else>
                  可发送 <strong>{{ directIdsCount }}</strong> 个用户，将发送「{{ contactLabel }}」
                </span>
              </div>

              <el-button
                type="warning"
                :loading="directSubmitting"
                :disabled="finalSecIds.length === 0"
                @click="submitDirect"
                size="large"
                style="width:100%; margin-top:12px"
              >
                <el-icon><Promotion /></el-icon>
                立即发送（{{ directIdsCount }} 人）
              </el-button>

              <!-- 直接联系结果 -->
              <el-alert
                v-if="directResult"
                :title="`发送完成：私信 ${directResult.dm_sent} 条，评论 ${directResult.comment_sent} 条，失败 ${directResult.dm_failed + directResult.comment_failed} 条`"
                :type="(directResult.dm_sent + directResult.comment_sent) > 0 ? 'success' : 'warning'"
                show-icon :closable="true"
                class="mt-3"
                @close="directResult = null"
              />
            </el-tab-pane>
          </el-tabs>
        </el-card>
      </el-col>

      <!-- 右侧：任务进度 -->
      <el-col :span="13">
        <el-card shadow="never" header="任务进度">
          <div v-if="tasks.length === 0" class="empty">
            <el-empty description="暂无任务记录" :image-size="80" />
          </div>
          <el-timeline v-else>
            <el-timeline-item
              v-for="t in tasks"
              :key="t.task_id"
              :type="timelineType(t.status)"
              :timestamp="formatDate(t.started_at)"
              placement="top"
            >
              <el-card shadow="never" class="task-card">
                <div class="task-header">
                  <el-tag :type="statusType(t.status)" size="small">{{ statusText(t.status) }}</el-tag>
                  <el-tag size="small" :type="t.search_type === 'video' ? 'warning' : 'primary'" effect="plain">
                    {{ t.search_type === 'video' ? '搜索作品' : '搜索用户' }}
                  </el-tag>
                  <span class="task-id">任务 #{{ t.task_id }}</span>
                </div>
                <el-row :gutter="8" class="task-stats mt-2">
                  <el-col :span="6">
                    <div class="stat-mini">
                      <div class="stat-mini-val">{{ t.total_found }}</div>
                      <div class="stat-mini-label">{{ t.search_type === 'video' ? '提取作者' : '搜索到' }}</div>
                    </div>
                  </el-col>
                  <el-col :span="6">
                    <div class="stat-mini">
                      <div class="stat-mini-val">{{ t.total_passed }}</div>
                      <div class="stat-mini-label">通过筛选</div>
                    </div>
                  </el-col>
                  <el-col :span="6">
                    <div class="stat-mini">
                      <div class="stat-mini-val">{{ t.total_sent }}</div>
                      <div class="stat-mini-label">发私信</div>
                    </div>
                  </el-col>
                  <el-col :span="6">
                    <div class="stat-mini">
                      <div class="stat-mini-val">{{ t.total_commented ?? 0 }}</div>
                      <div class="stat-mini-label">发评论</div>
                    </div>
                  </el-col>
                </el-row>
                <el-progress
                  v-if="t.status === 'running'"
                  :percentage="99" status="active"
                  :striped="true" :striped-flow="true" class="mt-2"
                />
                <el-alert
                  v-if="t.error_msg" :title="t.error_msg"
                  type="error" :closable="false" show-icon class="mt-2" size="small"
                />
              </el-card>
            </el-timeline-item>
          </el-timeline>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import {
  VideoPlay, Message, ChatDotRound, Connection,
  User, Promotion, InfoFilled, UploadFilled, CircleCheck, Search,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { taskApi, dmApi, userApi } from '@/api/index.js'
import dayjs from 'dayjs'

// ── Tab 状态 ─────────────────────────────────────────────────────────────────
const activeTab = ref('search_user')

// ── 搜索用户表单 ──────────────────────────────────────────────────────────────
const searchForm = ref({
  keywords: [], min_followers: 10000, min_total_likes: 10000,
  require_shop: false, contact_method: 'none',
})
const searchRules = { keywords: [{ required: true, message: '请至少输入一个关键词', trigger: 'change' }] }
const searchFormRef   = ref(null)
const searchSubmitting = ref(false)

async function submitSearch() {
  await searchFormRef.value.validate()
  searchSubmitting.value = true
  try {
    const res = await taskApi.run({ ...searchForm.value, search_type: 'user' })
    res.search_type = 'user'
    tasks.value.unshift(res)
    ElMessage.success(`任务 #${res.task_id} 已启动`)
    pollTask(res.task_id, 'user')
  } finally {
    searchSubmitting.value = false
  }
}

// ── 搜索作品表单 ──────────────────────────────────────────────────────────────
const videoForm = ref({
  keywords: [], min_followers: 10000, min_total_likes: 10000,
  require_shop: false, contact_method: 'none',
})
const videoFormRef    = ref(null)
const videoSubmitting = ref(false)

async function submitVideo() {
  await videoFormRef.value.validate()
  videoSubmitting.value = true
  try {
    const res = await taskApi.run({ ...videoForm.value, search_type: 'video' })
    res.search_type = 'video'
    tasks.value.unshift(res)
    ElMessage.success(`任务 #${res.task_id} 已启动`)
    pollTask(res.task_id, 'video')
  } finally {
    videoSubmitting.value = false
  }
}

// ── 直接联系表单 ──────────────────────────────────────────────────────────────
const inputMode = ref('manual')
const inputModeOptions = [
  { label: '手动输入', value: 'manual' },
  { label: '上传文件', value: 'upload' },
]

const directForm = ref({
  rawIds: '',
  contact_method: 'dm',
})
const parsedIds        = ref([])   // 文件解析出的 ID 列表
const resolvedItems    = ref([])   // 解析结果（含 nickname、status）
const resolving        = ref(false)
const directSubmitting = ref(false)
const directResult     = ref(null)
const uploadRef        = ref(null)

// 识别单个标识符格式
function detectFormat(inp) {
  if (!inp) return 'empty'
  if (inp.startsWith('MS4') && inp.length > 20) return 'sec_user_id'
  if (/douyin\.com\/user\//.test(inp)) return 'url'
  if (/^\d{6,20}$/.test(inp)) return 'short_id'   // 抖音号(6~12位)或 uid(10~20位)，统一走解析
  return 'unknown'
}

function formatLabel(fmt) {
  return { sec_user_id: 'sec_uid', url: 'URL', short_id: '抖音号/uid', unknown: '未知' }[fmt] || fmt
}

// 解析输入框中的原始文本为标识符列表
const rawInputList = computed(() => {
  const raw = directForm.value.rawIds.trim()
  if (!raw) return []
  return raw
    .split(/[\n,，]+/)
    .map(s => s.trim())
    .filter(s => s.length >= 6)
})

// 当前实际 ID 列表（手动 or 文件）
const inputList = computed(() =>
  inputMode.value === 'manual' ? rawInputList.value : parsedIds.value
)

// 输入列表的格式分布统计
const formatStats = computed(() => {
  const stats = { sec_user_id: 0, url: 0, short_id: 0, unknown: 0 }
  for (const inp of inputList.value) {
    const fmt = detectFormat(inp)
    const key = fmt === 'uid' ? 'short_id' : fmt   // uid 合并到 short_id 显示
    stats[key] = (stats[key] || 0) + 1
  }
  return stats
})

// 是否有需要解析的格式（纯数字：抖音号或uid）
const needsResolve = computed(() =>
  formatStats.value.short_id > 0
)

// 最终可发送的 sec_user_id 列表
const finalSecIds = computed(() => {
  if (resolvedItems.value.length > 0) {
    // 已解析过，使用解析结果
    return resolvedItems.value
      .filter(r => r.status === 'ok' && r.sec_user_id)
      .map(r => r.sec_user_id)
  }
  // 未解析，仅提取已知格式的
  return inputList.value.map(inp => {
    const fmt = detectFormat(inp)
    if (fmt === 'sec_user_id') return inp
    if (fmt === 'url') {
      const m = inp.match(/douyin\.com\/user\/(MS4[A-Za-z0-9_\-]+)/)
      return m ? m[1] : null
    }
    return null
  }).filter(Boolean)
})

const directIdsCount = computed(() => finalSecIds.value.length)

const contactLabel = computed(() => {
  const m = directForm.value.contact_method
  return { dm: '私信', comment: '评论最新作品', both: '私信 + 评论' }[m] || m
})

// 解析抖音号/uid → sec_user_id
async function resolveIds() {
  const list = inputList.value
  if (list.length === 0) { ElMessage.warning('请先输入用户标识'); return }
  resolving.value = true
  resolvedItems.value = []
  try {
    const res = await userApi.resolve(list)
    resolvedItems.value = res.results || []
    const ok  = resolvedItems.value.filter(r => r.status === 'ok').length
    const bad = resolvedItems.value.filter(r => r.status !== 'ok').length
    ElMessage.success(`解析完成：成功 ${ok} 个${bad > 0 ? `，失败 ${bad} 个` : ''}`)
  } catch (e) {
    ElMessage.error('解析失败：' + (e?.message || e))
  } finally {
    resolving.value = false
  }
}

// 文件上传处理
function handleFileChange(file) {
  resolvedItems.value = []
  const reader = new FileReader()
  reader.onload = (e) => {
    const text = e.target.result
    const lines = text.split(/[\n\r]+/).map(l => l.trim()).filter(Boolean)
    const ids = []
    for (const line of lines) {
      const cols = line.split(',')
      const candidate = cols[0].trim().replace(/^["']|["']$/g, '')
      // 接受 MS4 开头的 sec_user_id，抖音号，uid，主页 URL
      if (candidate.startsWith('MS4') ||
          /^\d{6,20}$/.test(candidate) ||
          /douyin\.com\/user\//.test(candidate)) {
        ids.push(candidate)
      }
    }
    parsedIds.value = ids.slice(0, 500)
    if (parsedIds.value.length === 0) {
      ElMessage.warning('未解析到有效 ID，请检查文件格式')
    } else {
      ElMessage.success(`解析 ${parsedIds.value.length} 条记录`)
    }
  }
  reader.readAsText(file.raw, 'utf-8')
}

function handleFileRemove() {
  parsedIds.value = []
  resolvedItems.value = []
}

// 清除解析缓存（输入变化时）
function onRawIdsChange() {
  resolvedItems.value = []
}

// 提交直接联系
async function submitDirect() {
  if (finalSecIds.value.length === 0) {
    if (needsResolve.value && resolvedItems.value.length === 0) {
      ElMessage.warning('有抖音号/uid 格式，请先点击「解析」转换为 sec_user_id')
    } else {
      ElMessage.warning('没有可发送的用户 ID')
    }
    return
  }
  directSubmitting.value = true
  directResult.value = null
  try {
    const res = await dmApi.contact(finalSecIds.value, directForm.value.contact_method)
    directResult.value = res
    const total = (res.dm_sent || 0) + (res.comment_sent || 0)
    if (total > 0) {
      ElMessage.success(`发送完成！私信 ${res.dm_sent || 0} 条，评论 ${res.comment_sent || 0} 条`)
    } else {
      ElMessage.warning('发送完成，但均未成功（可能已发过或达上限）')
    }
  } catch (e) {
    ElMessage.error('发送失败：' + (e?.message || e))
  } finally {
    directSubmitting.value = false
  }
}

// ── 任务进度 ───────────────────────────────────────────────────────────────────
const tasks = ref([])
let pollTimers = {}

function statusType(s) { return { running: 'warning', done: 'success', error: 'danger' }[s] || 'info' }
function statusText(s) { return { running: '执行中', done: '已完成', error: '失败' }[s] || s }
function timelineType(s) { return { running: 'warning', done: 'success', error: 'danger' }[s] || 'primary' }
function formatDate(d) { return d ? dayjs(d).format('MM-DD HH:mm:ss') : '—' }

function pollTask(taskId, searchType) {
  clearInterval(pollTimers[taskId])
  pollTimers[taskId] = setInterval(async () => {
    try {
      const res = await taskApi.status(taskId)
      res.search_type = searchType
      const idx = tasks.value.findIndex(t => t.task_id === taskId)
      if (idx >= 0) tasks.value[idx] = res
      if (res.status !== 'running') clearInterval(pollTimers[taskId])
    } catch { clearInterval(pollTimers[taskId]) }
  }, 3000)
}

onMounted(() => {})
onUnmounted(() => Object.values(pollTimers).forEach(clearInterval))
</script>

<style scoped>
.tasks-page {}
.page-title { font-size: 20px; font-weight: 600; margin-bottom: 16px; color: #1a1a1a; }
.form-card { border-radius: 8px; }
.main-tabs { margin-bottom: 0; }
.tab-icon { margin-right: 4px; vertical-align: middle; }

.mb-3 { margin-bottom: 12px; }
.mt-1 { margin-top: 6px; }
.mt-2 { margin-top: 8px; }
.mt-3 { margin-top: 12px; }
.div-sm { margin: 12px 0 8px; }
.unit-hint { font-size: 12px; color: #8c8c8c; margin-left: 8px; }

/* 直接联系相关 */
.upload-area { width: 100%; }
.upload-tip { font-size: 12px; color: #8c8c8c; margin-top: 4px; line-height: 1.5; }
.field-hint { font-size: 12px; color: #8c8c8c; display: flex; align-items: center; gap: 4px; }
.parsed-preview { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
.id-chip {
  background: #f0f7ff; color: #1677ff; border-radius: 4px;
  padding: 2px 8px; font-size: 12px; font-family: monospace;
}
.more-hint { font-size: 12px; color: #8c8c8c; }

/* 格式统计行 */
.format-stats {
  display: flex; align-items: center; justify-content: space-between;
  margin: 6px 0 10px; flex-wrap: wrap; gap: 8px;
}
.format-tags { display: flex; flex-wrap: wrap; gap: 6px; }

/* 解析结果列表 */
.resolve-result { margin-top: 4px; }
.resolve-row {
  display: flex; align-items: center; gap: 8px;
  padding: 4px 0; font-size: 12px;
  border-bottom: 1px solid #f0f0f0;
}
.resolve-input {
  font-family: monospace; color: #595959;
  min-width: 90px; flex-shrink: 0;
}

.id-count-bar {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; background: #fffbe6; border: 1px solid #ffe58f;
  border-radius: 6px; font-size: 13px; color: #664700; margin-top: 8px;
}

/* 任务进度 */
.empty { padding: 20px 0; }
.task-card { background: #fafafa; border-radius: 6px; }
.task-header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.task-id { font-size: 13px; color: #8c8c8c; }
.task-stats {}
.stat-mini { text-align: center; padding: 8px 0; }
.stat-mini-val   { font-size: 22px; font-weight: 700; color: #1677ff; }
.stat-mini-label { font-size: 12px; color: #8c8c8c; margin-top: 2px; }
</style>
