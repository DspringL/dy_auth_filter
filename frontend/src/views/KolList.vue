<template>
  <div class="kol-list">
    <div class="page-header">
      <span class="page-title">达人列表</span>
      <div class="header-actions">
        <el-button :icon="Refresh" @click="loadData">刷新</el-button>
        <el-button
          type="primary"
          :icon="Message"
          :disabled="!selectedRows.length"
          @click="() => openContactDialog('dm')"
        >
          私信 ({{ selectedRows.length }})
        </el-button>
        <el-button
          type="success"
          :icon="ChatDotRound"
          :disabled="!selectedRows.length"
          @click="() => openContactDialog('comment')"
        >
          评论 ({{ selectedRows.length }})
        </el-button>
        <el-button
          type="warning"
          :disabled="!selectedRows.length"
          @click="() => openContactDialog('both')"
        >
          两种都发
        </el-button>
        <el-button
          type="danger"
          :icon="Delete"
          :disabled="!selectedRows.length"
          @click="deleteBatch"
        >
          删除 ({{ selectedRows.length }})
        </el-button>
      </div>
    </div>

    <!-- 搜索栏 -->
    <el-card shadow="never" class="filter-card">
      <el-form :model="filters" inline>
        <el-form-item label="昵称">
          <el-input v-model="filters.keyword" placeholder="模糊搜索昵称" clearable style="width:160px" />
        </el-form-item>
        <el-form-item label="私信状态">
          <el-select v-model="filters.dm_status" clearable placeholder="全部" style="width:120px">
            <el-option label="待发送" value="pending" />
            <el-option label="已发送" value="sent" />
            <el-option label="失败" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item label="粉丝下限">
          <el-input-number v-model="filters.min_followers" :min="0" :step="10000" controls-position="right" style="width:130px" />
          <span style="font-size:12px;color:#8c8c8c;margin-left:6px">0=不限</span>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 表格 -->
    <el-card shadow="never" class="table-card">
      <el-table
        v-loading="loading"
        :data="list"
        @selection-change="handleSelectionChange"
        row-key="id"
        stripe
        style="width:100%"
      >
        <el-table-column type="selection" width="46" />

        <!-- 达人信息 -->
        <el-table-column label="达人" min-width="220" fixed="left">
          <template #default="{ row }">
            <div class="kol-cell">
              <el-avatar :src="row.avatar_url" :size="40" shape="circle">
                <el-icon><User /></el-icon>
              </el-avatar>
              <div class="kol-info">
                <div class="kol-name">
                  {{ row.nickname || '—' }}
                  <el-icon v-if="row.is_star" color="#faad14" size="13"><StarFilled /></el-icon>
                </div>
                <div class="kol-id">抖音号：{{ row.short_id || row.uid || '—' }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <!-- 粉丝数 -->
        <el-table-column label="粉丝数" prop="follower_count" width="110" sortable>
          <template #default="{ row }">
            <span class="num-cell">{{ formatNum(row.follower_count) }}</span>
          </template>
        </el-table-column>

        <!-- 总获赞 -->
        <el-table-column label="总获赞" prop="total_favorited" width="110" sortable>
          <template #default="{ row }">
            <span class="num-cell">{{ formatNum(row.total_favorited) }}</span>
          </template>
        </el-table-column>

        <!-- 作品数 -->
        <el-table-column label="作品数" prop="aweme_count" width="90" sortable />

        <!-- IP 属地 -->
        <el-table-column label="IP属地" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.ip_location" size="small" type="info" effect="plain">
              {{ row.ip_location }}
            </el-tag>
            <span v-else class="text-gray">—</span>
          </template>
        </el-table-column>

        <!-- 带货橱窗 -->
        <el-table-column label="橱窗" width="70" align="center">
          <template #default="{ row }">
            <el-icon v-if="row.has_shop" color="#52c41a" size="18"><CircleCheckFilled /></el-icon>
            <el-icon v-else color="#d9d9d9" size="18"><CircleCloseFilled /></el-icon>
          </template>
        </el-table-column>

        <!-- 私信状态 -->
        <el-table-column label="私信" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.dm_status)" size="small" effect="plain">
              {{ statusTagLabel(row.dm_status) }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 评论状态 -->
        <el-table-column label="评论" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.comment_status)" size="small" effect="plain">
              {{ statusTagLabel(row.comment_status) }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 搜索词 -->
        <el-table-column label="来源关键词" prop="search_keyword" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.search_keyword" size="small" type="info" effect="plain">
              {{ row.search_keyword }}
            </el-tag>
          </template>
        </el-table-column>

        <!-- 采集时间 -->
        <el-table-column label="采集时间" width="160">
          <template #default="{ row }">
            {{ formatDate(row.collected_at) }}
          </template>
        </el-table-column>

        <!-- 操作 -->
        <el-table-column label="操作" width="200" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              text
              type="primary"
              :icon="View"
              @click="$router.push(`/kols/${row.id}`)"
            >详情</el-button>
            <el-button
              size="small"
              text
              :icon="Link"
              @click="openProfile(row)"
            >主页</el-button>
            <el-button
              size="small"
              text
              type="danger"
              :icon="Delete"
              @click="deleteSingle(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <!-- 分页 -->
      <div class="pagination">
        <el-pagination
          v-model:current-page="pagination.page"
          v-model:page-size="pagination.page_size"
          :total="pagination.total"
          :page-sizes="[20, 50, 100]"
          layout="total, sizes, prev, pager, next"
          @change="loadData"
        />
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { Refresh, Message, View, Link, ChatDotRound, Delete } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kolApi, dmApi } from '@/api/index.js'
import { isTauri } from '@/tauri.js'
import dayjs from 'dayjs'

const loading = ref(false)
const list = ref([])
const selectedRows = ref([])   // 存整行对象，方便同时拿 id 和 sec_user_id

const filters = reactive({ keyword: '', dm_status: '', min_followers: 0 })
const pagination = reactive({ page: 1, page_size: 20, total: 0 })

// 从 selectedRows 提取 sec_user_id 列表（用于私信/评论）
const selectedIds = () => selectedRows.value.map(r => r.sec_user_id)
// 从 selectedRows 提取 id 列表（用于删除）
const selectedDbIds = () => selectedRows.value.map(r => r.id)

function formatNum(n) {
  if (!n) return '0'
  if (n >= 100_000_000) return (n / 100_000_000).toFixed(1) + '亿'
  if (n >= 10_000) return (n / 10_000).toFixed(1) + '万'
  return String(n)
}
function formatDate(d) {
  return d ? dayjs(d).format('MM-DD HH:mm') : '—'
}
function statusTagType(s) {
  return { pending: 'warning', sent: 'success', failed: 'danger' }[s] || 'info'
}
function statusTagLabel(s) {
  return { pending: '待发', sent: '已发', failed: '失败' }[s] || s
}

// 打开达人主页：Tauri 环境用 shell.open，浏览器用 window.open
async function openProfile(row) {
  if (!row.profile_url) {
    ElMessage.warning('该达人无主页链接')
    return
  }
  if (isTauri()) {
    try {
      const { open } = await import('@tauri-apps/plugin-shell')
      await open(row.profile_url)
    } catch (e) {
      // fallback
      window.open(row.profile_url, '_blank')
    }
  } else {
    window.open(row.profile_url, '_blank')
  }
}

function handleSelectionChange(rows) {
  selectedRows.value = rows
}

// 单个删除
async function deleteSingle(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除达人「${row.nickname || row.sec_user_id}」？此操作不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
    await kolApi.delete(row.id)
    ElMessage.success('已删除')
    loadData()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

// 批量删除
async function deleteBatch() {
  const ids = selectedDbIds()
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确认删除选中的 ${ids.length} 位达人？此操作不可恢复。`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }
    )
    await kolApi.deleteBatch(ids)
    ElMessage.success(`已删除 ${ids.length} 位达人`)
    selectedRows.value = []
    loadData()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

async function openContactDialog(method) {
  const ids = selectedIds()
  if (!ids.length) return
  const labelMap = { dm: '私信', comment: '评论最新作品', both: '私信 + 评论' }
  try {
    await ElMessageBox.confirm(
      `确认对选中的 ${ids.length} 位达人发送「${labelMap[method]}」？`,
      '批量联系',
      { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
    )
    const res = await dmApi.contact(ids, method)
    const parts = []
    if (res.dm_sent)      parts.push(`私信 ${res.dm_sent} 条`)
    if (res.comment_sent) parts.push(`评论 ${res.comment_sent} 条`)
    ElMessage.success(`发送完成：${parts.join('，') || '无新发送'}`)
    loadData()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('发送失败')
  }
}

async function loadData() {
  loading.value = true
  try {
    const params = {
      page: pagination.page,
      page_size: pagination.page_size,
      ...(filters.keyword && { keyword: filters.keyword }),
      ...(filters.dm_status && { dm_status: filters.dm_status }),
      ...(filters.min_followers > 0 && { min_followers: filters.min_followers }),
    }
    const res = await kolApi.list(params)
    list.value = res.items
    pagination.total = res.total
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  pagination.page = 1
  loadData()
}

function resetFilters() {
  filters.keyword = ''
  filters.dm_status = ''
  filters.min_followers = 0
  pagination.page = 1
  loadData()
}

onMounted(loadData)
</script>

<style scoped>
.kol-list {}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title { font-size: 20px; font-weight: 600; color: #1a1a1a; }
.header-actions { display: flex; gap: 8px; }

.filter-card { margin-bottom: 16px; border-radius: 8px; }
.filter-card .el-form { flex-wrap: wrap; gap: 0; }

.table-card { border-radius: 8px; }

.kol-cell { display: flex; align-items: center; gap: 10px; }
.kol-info { display: flex; flex-direction: column; }
.kol-name { font-weight: 500; font-size: 13px; display: flex; align-items: center; gap: 4px; }
.kol-id { font-size: 12px; color: #8c8c8c; margin-top: 2px; }

.num-cell { font-weight: 600; color: #1677ff; }
.text-gray { color: #bfbfbf; font-size: 13px; }

.pagination {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
}
</style>
