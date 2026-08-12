import { defineStore } from 'pinia'
import { ref } from 'vue'

/**
 * 任务列表全局 store
 * 切换菜单后任务数据不丢失
 */
export const useTaskStore = defineStore('tasks', () => {
  const tasks = ref([])

  function addTask(task) {
    tasks.value.unshift(task)
  }

  function updateTask(taskId, updates) {
    const idx = tasks.value.findIndex(t => t.task_id === taskId)
    if (idx !== -1) {
      tasks.value[idx] = { ...tasks.value[idx], ...updates }
    }
  }

  function clearTasks() {
    tasks.value = []
  }

  return { tasks, addTask, updateTask, clearTasks }
})
