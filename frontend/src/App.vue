<template>
  <!-- Tauri 桌面端：等后端就绪才展示正式界面 -->
  <SplashScreen
    v-if="showSplash"
    :progress="splashProgress"
    :status-text="splashText"
    :error="splashError"
    @retry="initBackend"
  />
  <router-view v-else />
</template>

<script setup>
import { ref, onMounted } from 'vue'
import SplashScreen from '@/components/SplashScreen.vue'
import { isTauri, waitForBackend } from '@/tauri.js'

const showSplash   = ref(false)
const splashProgress = ref(0)
const splashText   = ref('正在初始化...')
const splashError  = ref(false)

async function initBackend() {
  splashError.value  = false
  splashProgress.value = 10
  splashText.value   = '正在启动后端服务...'

  // 模拟进度：在等待过程中匀速增长到 90%
  const timer = setInterval(() => {
    if (splashProgress.value < 88) splashProgress.value += 3
  }, 600)

  const ready = await waitForBackend(60)
  clearInterval(timer)

  if (ready) {
    splashProgress.value = 100
    splashText.value = '启动成功，正在加载界面...'
    await new Promise(r => setTimeout(r, 600))
    showSplash.value = false
  } else {
    splashError.value = true
    splashText.value = '后端启动超时，请检查 Python 环境或重试'
  }
}

onMounted(async () => {
  if (isTauri()) {
    showSplash.value = true
    await initBackend()
  }
  // 浏览器开发模式：直接展示，不显示 splash
})
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f2f5;
}
</style>
