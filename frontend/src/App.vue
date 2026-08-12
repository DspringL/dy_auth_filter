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

  // 进度动画：前 30s 匀速到 85%，之后放慢
  let tick = 0
  const timer = setInterval(() => {
    tick++
    if (tick < 20 && splashProgress.value < 60) {
      splashProgress.value += 3        // 前 20 次：快速涨
    } else if (splashProgress.value < 85) {
      splashProgress.value += 0.8      // 后续：放慢，留空间给真实就绪
    }
    // 动态提示文字
    if (tick === 5)  splashText.value = '后端初始化中，首次启动需要 10-20 秒...'
    if (tick === 15) splashText.value = '正在加载依赖模块，请稍候...'
    if (tick === 30) splashText.value = '快好了，再等一会儿...'
  }, 600)

  const ready = await waitForBackend(90)
  clearInterval(timer)

  if (ready) {
    splashProgress.value = 100
    splashText.value = '启动成功，正在加载界面...'
    await new Promise(r => setTimeout(r, 500))
    showSplash.value = false
  } else {
    splashError.value = true
    splashText.value = '后端启动超时（90s），请点击重试'
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
