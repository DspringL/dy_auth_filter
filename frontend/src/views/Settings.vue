<template>
  <div class="settings-page">
    <div class="page-title">系统设置</div>

    <el-row :gutter="16">
      <!-- 左列：登录 -->
      <el-col :span="12">
        <!-- 登录状态 -->
        <el-card shadow="never" header="抖音账号登录" class="card">
          <el-alert
            v-if="store.loginStatus.logged_in"
            title="当前账号已登录，Cookie 有效"
            type="success" show-icon :closable="false" class="mb-3"
          />
          <el-alert
            v-else
            title="当前未登录或 Cookie 已失效"
            description="点击下方按钮，在弹出的浏览器中扫码完成登录"
            type="warning" show-icon :closable="false" class="mb-3"
          />
          <el-button
            type="primary" size="large" :loading="store.loginLoading"
            :icon="Key" @click="handleLogin" style="width:100%"
          >
            {{ store.loginStatus.logged_in ? '重新登录（刷新 Cookie）' : '扫码登录抖音' }}
          </el-button>
          <el-divider />
          <el-descriptions :column="1" size="small">
            <el-descriptions-item label="登录状态">
              <el-tag :type="store.loginStatus.logged_in ? 'success' : 'danger'" effect="plain">
                {{ store.loginStatus.logged_in ? '已登录' : '未登录' }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="说明">{{ store.loginStatus.message }}</el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-col>

      <!-- 右列：私信 + 评论 -->
      <el-col :span="12">
        <!-- 私信配置 -->
        <el-card shadow="never" class="card">
          <template #header>
            <div class="card-header">
              <span>私信配置</span>
              <el-tag size="small" type="info" effect="plain">修改后立即生效</el-tag>
            </div>
          </template>
          <el-form :model="form" label-width="120px" :disabled="saving">
            <el-form-item label="私信模板">
              <el-input
                v-model="form['dm.template']"
                type="textarea"
                :rows="3"
                placeholder="请写成一行，不要换行——抖音限制对方回复前只能发一条消息"
              />
              <div class="field-hint">
                支持 <code>{nickname}</code> 占位符 ·
                <el-text type="danger" size="small">⚠ 请勿换行，否则会被拆成多条触发发送限制</el-text>
              </div>
            </el-form-item>
            <el-form-item label="每日上限（条）">
              <el-input-number
                v-model="form['dm.daily_limit']"
                :min="1" :max="100" controls-position="right" style="width:120px"
              />
            </el-form-item>
            <el-form-item label="间隔下限（秒）">
              <el-input-number
                v-model="form['dm.interval_min']"
                :min="10" :max="600" controls-position="right" style="width:120px"
              />
            </el-form-item>
            <el-form-item label="间隔上限（秒）">
              <el-input-number
                v-model="form['dm.interval_max']"
                :min="10" :max="3600" controls-position="right" style="width:120px"
              />
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 评论配置 -->
        <el-card shadow="never" class="card mt-4">
          <template #header>
            <div class="card-header">
              <span>评论配置</span>
              <el-tag size="small" type="info" effect="plain">修改后立即生效</el-tag>
            </div>
          </template>
          <el-form :model="form" label-width="120px" :disabled="saving">
            <el-form-item label="评论模板">
              <el-input
                v-model="form['comment.template']"
                type="textarea"
                :rows="3"
                placeholder="请写成一行，建议不超过 100 字"
              />
              <div class="field-hint">
                支持 <code>{nickname}</code> 占位符 ·
                <el-text type="warning" size="small">建议单行，不超过 100 字</el-text>
              </div>
            </el-form-item>
            <el-form-item label="每日上限（条）">
              <el-input-number
                v-model="form['comment.daily_limit']"
                :min="1" :max="200" controls-position="right" style="width:120px"
              />
            </el-form-item>
            <el-form-item label="间隔下限（秒）">
              <el-input-number
                v-model="form['comment.interval_min']"
                :min="10" :max="600" controls-position="right" style="width:120px"
              />
            </el-form-item>
            <el-form-item label="间隔上限（秒）">
              <el-input-number
                v-model="form['comment.interval_max']"
                :min="10" :max="3600" controls-position="right" style="width:120px"
              />
            </el-form-item>
          </el-form>
        </el-card>
      </el-col>
    </el-row>

    <!-- 底部保存按钮 -->
    <div class="save-bar">
      <el-button @click="loadConfig" :loading="loading" :icon="Refresh">
        重置为已保存值
      </el-button>
      <el-button type="primary" @click="handleSave" :loading="saving" :icon="Check" size="large">
        保存所有设置
      </el-button>
    </div>

    <!-- 说明 -->
    <el-alert
      class="mt-4"
      title="说明：私信和评论配置保存后，下次联系达人时自动使用新值。settings.yaml 中的值作为初始默认值，不会被覆盖。"
      type="info" :closable="false" show-icon
    />
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { Key, Refresh, Check } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { useAppStore } from '@/stores/app.js'
import { configApi } from '@/api/index.js'

const store   = useAppStore()
const loading = ref(false)
const saving  = ref(false)

// 与后端 CONFIG_KEYS 对应的表单
const form = reactive({
  'dm.template':            '',
  'dm.daily_limit':         20,
  'dm.interval_min':        60,
  'dm.interval_max':        180,
  'comment.template':       '',
  'comment.daily_limit':    30,
  'comment.interval_min':   45,
  'comment.interval_max':   120,
})

async function loadConfig() {
  loading.value = true
  try {
    const data = await configApi.get()
    // 将后端返回的配置填入表单
    for (const key of Object.keys(form)) {
      if (key in data) {
        form[key] = data[key]
      }
    }
  } catch (e) {
    ElMessage.error('加载配置失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

async function handleSave() {
  // 校验间隔：min <= max
  if (form['dm.interval_min'] > form['dm.interval_max']) {
    ElMessage.warning('私信间隔：下限不能大于上限')
    return
  }
  if (form['comment.interval_min'] > form['comment.interval_max']) {
    ElMessage.warning('评论间隔：下限不能大于上限')
    return
  }
  saving.value = true
  try {
    const payload = { ...form }
    const res = await configApi.save(payload)
    ElMessage.success(`已保存 ${res.saved.length} 项配置`)
  } catch (e) {
    ElMessage.error('保存失败：' + (e?.message || e))
  } finally {
    saving.value = false
  }
}

let loginPollTimer = null
onMounted(async () => {
  await store.fetchLoginStatus()
  await loadConfig()
})
onUnmounted(() => { if (loginPollTimer) clearInterval(loginPollTimer) })

async function handleLogin() {
  const res = await store.triggerLogin()
  ElMessage.info(res?.message || '请查看弹出的浏览器窗口扫码')
  if (loginPollTimer) clearInterval(loginPollTimer)
  let cnt = 0
  loginPollTimer = setInterval(async () => {
    cnt++
    await store.fetchLoginStatus()
    if (store.loginStatus.logged_in) { clearInterval(loginPollTimer); ElMessage.success('登录成功！') }
    if (cnt >= 180) clearInterval(loginPollTimer)
  }, 3000)
}
</script>

<style scoped>
.settings-page { padding-bottom: 80px; }
.page-title { font-size: 20px; font-weight: 600; margin-bottom: 16px; color: #1a1a1a; }
.card { border-radius: 8px; }
.mt-4 { margin-top: 16px; }
.mb-3 { margin-bottom: 12px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.field-hint { font-size: 12px; color: #8c8c8c; margin-top: 4px; }
code { background: #f5f5f5; padding: 2px 6px; border-radius: 4px; font-size: 12px; }

.save-bar {
  position: fixed;
  bottom: 0; left: 220px; right: 0;
  padding: 14px 32px;
  background: #fff;
  border-top: 1px solid #e8e8e8;
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  z-index: 100;
  box-shadow: 0 -2px 8px rgba(0,0,0,.06);
}
</style>
