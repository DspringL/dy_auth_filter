<template>
  <div class="splash">
    <div class="splash-inner">
      <div class="logo-wrap">
        <!-- 抖音风格音符 logo -->
        <svg width="72" height="72" viewBox="0 0 512 512" fill="none" xmlns="http://www.w3.org/2000/svg">
          <rect width="512" height="512" rx="112" fill="#010101"/>
          <!-- 青色阴影 -->
          <g transform="translate(14,-14)" opacity="0.85">
            <ellipse cx="198" cy="362" rx="66" ry="66" fill="#2EE0D1"/>
            <rect x="250" y="108" width="56" height="200" fill="#2EE0D1"/>
            <rect x="250" y="108" width="126" height="112" fill="#2EE0D1"/>
            <ellipse cx="376" cy="164" rx="56" ry="56" fill="#2EE0D1"/>
          </g>
          <!-- 红色阴影 -->
          <g transform="translate(-14,14)" opacity="0.85">
            <ellipse cx="198" cy="362" rx="66" ry="66" fill="#FE2C55"/>
            <rect x="250" y="108" width="56" height="200" fill="#FE2C55"/>
            <rect x="250" y="108" width="126" height="112" fill="#FE2C55"/>
            <ellipse cx="376" cy="164" rx="56" ry="56" fill="#FE2C55"/>
          </g>
          <!-- 白色主体 -->
          <ellipse cx="198" cy="362" rx="66" ry="66" fill="white"/>
          <rect x="250" y="108" width="56" height="200" fill="white"/>
          <rect x="250" y="108" width="126" height="112" fill="white"/>
          <ellipse cx="376" cy="164" rx="56" ry="56" fill="white"/>
        </svg>
      </div>
      <h1 class="title">抖音达人联络官</h1>
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
