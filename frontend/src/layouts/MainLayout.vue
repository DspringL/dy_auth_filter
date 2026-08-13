<template>
  <el-container class="layout-container">
    <!-- 侧边栏 -->
    <el-aside :width="collapsed ? '64px' : '220px'" class="sidebar">
      <div class="logo" :class="{ collapsed }">
        <el-icon size="24" color="#fff"><VideoCamera /></el-icon>
        <span v-if="!collapsed" class="logo-text">抖音达人联络官</span>
      </div>

      <el-menu
        :default-active="$route.path"
        router
        background-color="#001529"
        text-color="#ffffffa6"
        active-text-color="#ffffff"
        :collapse="collapsed"
        :collapse-transition="false"
      >
        <el-menu-item index="/dashboard">
          <el-icon><DataBoard /></el-icon>
          <template #title>数据概览</template>
        </el-menu-item>
        <el-menu-item index="/kols">
          <el-icon><User /></el-icon>
          <template #title>达人列表</template>
        </el-menu-item>
        <el-menu-item index="/tasks">
          <el-icon><Operation /></el-icon>
          <template #title>任务管理</template>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <template #title>系统设置</template>
        </el-menu-item>
      </el-menu>

      <div class="collapse-btn" @click="collapsed = !collapsed">
        <el-icon><component :is="collapsed ? 'Expand' : 'Fold'" /></el-icon>
      </div>
    </el-aside>

    <!-- 主内容区 -->
    <el-container>
      <!-- 顶栏 -->
      <el-header class="header">
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item>{{ $route.meta.title }}</el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <!-- 登录状态指示 -->
          <el-tooltip :content="store.loginStatus.message">
            <el-tag
              :type="store.loginStatus.logged_in ? 'success' : 'danger'"
              effect="plain"
              class="login-tag"
            >
              <el-icon class="mr-1">
                <component :is="store.loginStatus.logged_in ? 'CircleCheck' : 'CircleClose'" />
              </el-icon>
              {{ store.loginStatus.logged_in ? '抖音已登录' : '未登录' }}
            </el-tag>
          </el-tooltip>

          <el-button
            v-if="!store.loginStatus.logged_in"
            type="primary"
            size="small"
            :loading="store.loginLoading"
            @click="handleLogin"
          >
            扫码登录
          </el-button>

          <el-button
            v-else
            type="danger"
            plain
            size="small"
            :loading="store.logoutLoading"
            @click="handleLogout"
          >
            退出登录
          </el-button>

          <el-button size="small" :icon="Refresh" circle @click="refreshStatus" />
        </div>
      </el-header>

      <!-- 页面内容 -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAppStore } from '@/stores/app.js'

const store = useAppStore()
const collapsed = ref(false)

// 登录轮询定时器
let loginPollTimer = null

onMounted(async () => {
  await store.fetchLoginStatus()
  await store.fetchStats()
})

onUnmounted(() => {
  if (loginPollTimer) clearInterval(loginPollTimer)
})

async function handleLogin() {
  const res = await store.triggerLogin()
  ElMessage.info(res?.message || '请查看浏览器弹出的窗口，使用抖音 App 扫码')

  // 清除旧轮询
  if (loginPollTimer) clearInterval(loginPollTimer)

  // 每 3 秒轮询一次登录状态，最多轮询 180 次（约 9 分钟）
  let pollCount = 0
  loginPollTimer = setInterval(async () => {
    pollCount++
    await store.fetchLoginStatus()

    if (store.loginStatus.logged_in) {
      clearInterval(loginPollTimer)
      loginPollTimer = null
      ElMessage.success('抖音登录成功！')
      await store.fetchStats()
      return
    }

    if (pollCount >= 180) {
      clearInterval(loginPollTimer)
      loginPollTimer = null
      ElMessage.warning('登录超时，请手动点击刷新按钮或重新触发扫码')
    }
  }, 3000)
}

async function refreshStatus() {
  await store.fetchLoginStatus()
  await store.fetchStats()
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm(
      '退出后将清除服务浏览器中的抖音登录态，后续任务需要重新扫码登录。',
      '确认退出登录',
      { type: 'warning', confirmButtonText: '退出登录', cancelButtonText: '取消' },
    )
    if (loginPollTimer) {
      clearInterval(loginPollTimer)
      loginPollTimer = null
    }
    await store.logout()
    ElMessage.success('已退出登录')
  } catch (error) {
    if (error !== 'cancel' && error !== 'close') throw error
  }
}
</script>

<style scoped>
.layout-container { height: 100vh; }

.sidebar {
  background: #001529;
  transition: width 0.2s;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid #ffffff14;
}
.logo-text {
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}
.logo.collapsed { justify-content: center; padding: 0; }

.el-menu { border-right: none; flex: 1; }

.collapse-btn {
  height: 48px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ffffffa6;
  cursor: pointer;
  border-top: 1px solid #ffffff14;
  transition: color 0.2s;
}
.collapse-btn:hover { color: #fff; }

.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(0,21,41,.12);
  height: 56px;
}
.header-right { display: flex; align-items: center; gap: 12px; }
.login-tag { cursor: default; }
.mr-1 { margin-right: 4px; }

.main-content {
  background: #f0f2f5;
  padding: 24px;
  overflow-y: auto;
}
</style>
