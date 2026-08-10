<template>
  <div class="splash">
    <div class="splash-inner">
      <div class="logo-wrap">
        <el-icon size="64" color="#1677ff"><VideoCamera /></el-icon>
      </div>
      <h1 class="title">抖音带货达人系统</h1>
      <p class="subtitle">正在启动后端服务，请稍候...</p>

      <el-progress
        :percentage="progress"
        :status="error ? 'exception' : progress >= 100 ? 'success' : undefined"
        :striped="progress < 100 && !error"
        :striped-flow="progress < 100 && !error"
        stroke-width="6"
        class="progress-bar"
      />

      <p class="status-text" :class="{ error }">{{ statusText }}</p>

      <el-button
        v-if="error"
        type="primary"
        @click="$emit('retry')"
        style="margin-top: 16px"
      >
        重试
      </el-button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  progress: { type: Number, default: 0 },
  statusText: { type: String, default: '初始化...' },
  error: { type: Boolean, default: false },
})
defineEmits(['retry'])
</script>

<style scoped>
.splash {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #001529 0%, #003a70 100%);
  z-index: 9999;
}
.splash-inner {
  text-align: center;
  width: 380px;
}
.logo-wrap {
  margin-bottom: 16px;
}
.title {
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 8px;
}
.subtitle {
  color: rgba(255,255,255,.6);
  font-size: 14px;
  margin: 0 0 24px;
}
.progress-bar {
  margin-bottom: 12px;
}
.status-text {
  color: rgba(255,255,255,.7);
  font-size: 13px;
}
.status-text.error {
  color: #ff7875;
}
</style>
