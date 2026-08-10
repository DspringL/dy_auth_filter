<template>
  <div class="dashboard">
    <div class="page-title">数据概览</div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6" v-for="item in statCards" :key="item.key">
        <el-card class="stat-card" shadow="never">
          <div class="stat-inner">
            <div class="stat-icon" :style="{ background: item.color }">
              <el-icon size="24" color="#fff"><component :is="item.icon" /></el-icon>
            </div>
            <div class="stat-info">
              <div class="stat-value">{{ stats[item.key] ?? 0 }}</div>
              <div class="stat-label">{{ item.label }}</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 快捷入口 -->
    <el-row :gutter="16" class="mt-4">
      <el-col :span="8">
        <el-card shadow="never" class="shortcut-card" @click="$router.push('/tasks')">
          <div class="shortcut-inner">
            <el-icon size="28" color="#1677ff"><Operation /></el-icon>
            <div>
              <div class="shortcut-title">发起搜索任务</div>
              <div class="shortcut-desc">搜索带货达人、筛选、私信或评论</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="shortcut-card" @click="$router.push('/kols')">
          <div class="shortcut-inner">
            <el-icon size="28" color="#52c41a"><User /></el-icon>
            <div>
              <div class="shortcut-title">浏览达人列表</div>
              <div class="shortcut-desc">查看画像、联系达人、访问主页</div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="shortcut-card" @click="$router.push('/settings')">
          <div class="shortcut-inner">
            <el-icon size="28" color="#faad14"><Setting /></el-icon>
            <div>
              <div class="shortcut-title">系统设置</div>
              <div class="shortcut-desc">修改筛选规则、私信和评论模板</div>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { statsApi } from '@/api/index.js'
import { ref } from 'vue'

const stats = ref({ total_kols: 0, dm_sent: 0, dm_pending: 0, has_shop: 0 })
const statCards = [
  { key: 'total_kols',  label: '收录达人',   icon: 'User',        color: '#1677ff' },
  { key: 'dm_sent',     label: '已发私信',   icon: 'Message',     color: '#52c41a' },
  { key: 'dm_pending',  label: '待发私信',   icon: 'Bell',        color: '#faad14' },
  { key: 'has_shop',    label: '开橱窗达人', icon: 'ShoppingBag', color: '#f5222d' },
]

onMounted(async () => {
  stats.value = await statsApi.get()
})
</script>

<style scoped>
.page-title { font-size: 20px; font-weight: 600; margin-bottom: 20px; color: #1a1a1a; }
.stat-row { margin-bottom: 0; }
.stat-card { border-radius: 8px; }
.stat-inner { display: flex; align-items: center; gap: 16px; padding: 4px 0; }
.stat-icon { width: 52px; height: 52px; border-radius: 10px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; }
.stat-value { font-size: 28px; font-weight: 700; color: #1a1a1a; line-height: 1; }
.stat-label { font-size: 13px; color: #8c8c8c; margin-top: 4px; }
.mt-4 { margin-top: 16px; }

.shortcut-card {
  border-radius: 8px;
  cursor: pointer;
  transition: box-shadow 0.2s, transform 0.15s;
}
.shortcut-card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,.12);
  transform: translateY(-2px);
}
.shortcut-inner {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 6px 0;
}
.shortcut-title { font-size: 15px; font-weight: 600; color: #1a1a1a; }
.shortcut-desc  { font-size: 12px; color: #8c8c8c; margin-top: 3px; }
</style>
