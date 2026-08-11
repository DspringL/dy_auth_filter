<template>
  <div v-loading="loading" class="kol-detail">
    <div class="page-header">
      <el-button :icon="ArrowLeft" @click="$router.back()">返回</el-button>
      <span class="page-title">达人详情</span>
    </div>

    <template v-if="kol">
      <!-- 达人画像卡片 -->
      <el-row :gutter="16">
        <el-col :span="8">
          <el-card shadow="never" class="profile-card">
            <div class="profile-header">
              <el-avatar :src="kol.avatar_url" :size="88" shape="circle" class="avatar">
                <el-icon size="32"><User /></el-icon>
              </el-avatar>
              <div class="profile-meta">
                <div class="name">
                  {{ kol.nickname || '—' }}
                  <el-icon v-if="kol.is_star" color="#faad14" size="16"><StarFilled /></el-icon>
                </div>
                <div class="short-id">抖音号：{{ kol.short_id || kol.uid || '—' }}</div>
                <div class="signature">{{ kol.signature || '暂无简介' }}</div>
              </div>
            </div>

            <!-- 数据指标 -->
            <el-divider />
            <el-row :gutter="8" class="metrics">
              <el-col :span="8" v-for="m in metrics" :key="m.key">
                <div class="metric-item">
                  <div class="metric-val">{{ formatNum(kol[m.key]) }}</div>
                  <div class="metric-label">{{ m.label }}</div>
                </div>
              </el-col>
            </el-row>

            <!-- 带货标签 -->
            <el-divider />
            <div class="tags-row">
              <el-tag v-if="kol.ip_location" type="info" effect="plain">
                <el-icon><Location /></el-icon> {{ kol.ip_location }}
              </el-tag>
              <el-tag v-if="kol.has_shop" type="success" effect="plain">
                <el-icon><ShoppingBag /></el-icon> 带货橱窗
              </el-tag>
              <el-tag v-if="kol.commerce_user_level > 0" type="warning" effect="plain">
                带货等级 Lv.{{ kol.commerce_user_level }}
              </el-tag>
              <el-tag v-if="kol.is_star" type="info" effect="plain">认证达人</el-tag>
              <el-tag type="info" effect="plain">来源：{{ kol.search_keyword || '—' }}</el-tag>
            </div>

            <!-- 操作按钮 -->
            <el-divider />
            <div class="action-btns">
              <el-button
                type="primary"
                :icon="Link"
                @click="openProfile"
                style="width:100%"
              >
                访问抖音主页
              </el-button>
              <el-button
                :type="kol.dm_status === 'sent' ? 'success' : 'default'"
                :icon="Message"
                :disabled="kol.dm_status === 'sent'"
                @click="sendContact('dm')"
                style="width:100%; margin-top:8px; margin-left:0"
              >
                {{ kol.dm_status === 'sent' ? '私信已发送' : '发送私信' }}
              </el-button>
              <el-button
                :type="kol.comment_status === 'sent' ? 'success' : 'default'"
                :icon="ChatDotRound"
                :disabled="kol.comment_status === 'sent'"
                @click="sendContact('comment')"
                style="width:100%; margin-top:8px; margin-left:0"
              >
                {{ kol.comment_status === 'sent' ? '评论已发送' : '评论最新作品' }}
              </el-button>
              <el-button
                type="warning"
                :disabled="kol.dm_status === 'sent' && kol.comment_status === 'sent'"
                @click="sendContact('both')"
                style="width:100%; margin-top:8px; margin-left:0"
              >
                私信 + 评论（都发）
              </el-button>
            </div>
          </el-card>
        </el-col>

        <el-col :span="16">
          <el-card shadow="never" class="info-card">
            <template #header>详细信息</template>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="sec_user_id">
                <el-text truncated style="max-width:200px">{{ kol.sec_user_id }}</el-text>
              </el-descriptions-item>
              <el-descriptions-item label="数字UID">{{ kol.uid || '—' }}</el-descriptions-item>
              <el-descriptions-item label="粉丝数">
                <span class="highlight">{{ formatNum(kol.follower_count) }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="关注数">{{ formatNum(kol.following_count) }}</el-descriptions-item>
              <el-descriptions-item label="总获赞">
                <span class="highlight">{{ formatNum(kol.total_favorited) }}</span>
              </el-descriptions-item>
              <el-descriptions-item label="作品数">{{ kol.aweme_count }}</el-descriptions-item>
              <el-descriptions-item label="带货橱窗">
                <el-tag :type="kol.has_shop ? 'success' : 'info'" effect="plain">
                  {{ kol.has_shop ? '已开通' : '未开通' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="带货等级">
                {{ kol.commerce_user_level > 0 ? `Lv.${kol.commerce_user_level}` : '无' }}
              </el-descriptions-item>
              <el-descriptions-item label="IP属地">
                <el-tag v-if="kol.ip_location" size="small" type="info" effect="plain">
                  {{ kol.ip_location }}
                </el-tag>
                <span v-else style="color:#bfbfbf">未知</span>
              </el-descriptions-item>
              <el-descriptions-item label="私信状态">
                <el-tag :type="statusTagType(kol.dm_status)" effect="plain">
                  {{ statusTagLabel(kol.dm_status) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="私信时间">
                {{ formatDate(kol.dm_sent_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="评论状态">
                <el-tag :type="statusTagType(kol.comment_status)" effect="plain">
                  {{ statusTagLabel(kol.comment_status) }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="评论时间">
                {{ formatDate(kol.comment_sent_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="采集时间">
                {{ formatDate(kol.collected_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="搜索关键词">
                {{ kol.search_keyword || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="主页链接" :span="2">
                <el-link :href="kol.profile_url" target="_blank" type="primary">
                  {{ kol.profile_url || '—' }}
                </el-link>
              </el-descriptions-item>
            </el-descriptions>
          </el-card>

          <!-- 个人简介 -->
          <el-card shadow="never" class="info-card mt-3" header="个人简介">
            <p class="signature-text">{{ kol.signature || '暂无简介' }}</p>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <el-empty v-else-if="!loading" description="未找到该达人" />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Link, Message, Location, ChatDotRound } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { kolApi, dmApi } from '@/api/index.js'
import { isTauri } from '@/tauri.js'
import dayjs from 'dayjs'

const route = useRoute()
const kol = ref(null)
const loading = ref(false)

const metrics = [
  { key: 'follower_count',  label: '粉丝' },
  { key: 'total_favorited', label: '获赞' },
  { key: 'aweme_count',     label: '作品' },
]

function formatNum(n) {
  if (!n) return '0'
  if (n >= 100_000_000) return (n / 100_000_000).toFixed(1) + '亿'
  if (n >= 10_000) return (n / 10_000).toFixed(1) + '万'
  return String(n)
}
function formatDate(d) {
  return d ? dayjs(d).format('YYYY-MM-DD HH:mm') : '—'
}
function statusTagType(s) {
  return { pending: 'warning', sent: 'success', failed: 'danger' }[s] || 'info'
}
function statusTagLabel(s) {
  return { pending: '待发', sent: '已发', failed: '失败' }[s] || s
}

async function openProfile() {
  if (!kol.value?.profile_url) {
    ElMessage.warning('该达人无主页链接')
    return
  }
  if (isTauri()) {
    try {
      const { open } = await import('@tauri-apps/plugin-shell')
      await open(kol.value.profile_url)
    } catch {
      window.open(kol.value.profile_url, '_blank')
    }
  } else {
    window.open(kol.value.profile_url, '_blank')
  }
}

async function sendContact(method) {
  if (!kol.value) return
  try {
    const res = await dmApi.contact([kol.value.sec_user_id], method)
    const parts = []
    if (res.dm_sent)      { parts.push(`私信已发送`); kol.value.dm_status = 'sent' }
    if (res.comment_sent) { parts.push(`评论已发送`); kol.value.comment_status = 'sent' }
    if (parts.length) ElMessage.success(parts.join('，'))
    else ElMessage.warning(`未发送（可能已发过或达到上限）`)
  } catch {}
}

async function loadKol() {
  loading.value = true
  try {
    kol.value = await kolApi.detail(route.params.id)
  } finally {
    loading.value = false
  }
}

onMounted(loadKol)
</script>

<style scoped>
.kol-detail {}
.page-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.page-title { font-size: 18px; font-weight: 600; }

.profile-card { border-radius: 8px; }
.profile-header { display: flex; gap: 16px; align-items: flex-start; }
.avatar { flex-shrink: 0; }
.profile-meta { flex: 1; }
.name { font-size: 18px; font-weight: 700; display: flex; align-items: center; gap: 6px; }
.short-id { font-size: 12px; color: #8c8c8c; margin-top: 4px; }
.signature { font-size: 13px; color: #595959; margin-top: 8px; line-height: 1.5; }

.metrics { text-align: center; }
.metric-item { padding: 4px 0; }
.metric-val { font-size: 20px; font-weight: 700; color: #1677ff; }
.metric-label { font-size: 12px; color: #8c8c8c; margin-top: 2px; }

.tags-row { display: flex; flex-wrap: wrap; gap: 8px; }
.action-btns {}

.info-card { border-radius: 8px; margin-bottom: 0; }
.mt-3 { margin-top: 12px; }

.highlight { font-weight: 600; color: #1677ff; }
.signature-text { color: #595959; line-height: 1.7; white-space: pre-wrap; }
</style>
