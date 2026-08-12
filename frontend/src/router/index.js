import { createRouter, createWebHistory } from 'vue-router'
import NProgress from 'nprogress'

const routes = [
  {
    path: '/',
    component: () => import('@/layouts/MainLayout.vue'),
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard',  name: 'Dashboard', component: () => import('@/views/Dashboard.vue'), meta: { title: '数据概览' } },
      { path: 'kols',       name: 'KolList',   component: () => import('@/views/KolList.vue'),   meta: { title: '达人列表' } },
      { path: 'kols/:id',   name: 'KolDetail', component: () => import('@/views/KolDetail.vue'), meta: { title: '达人详情' } },
      { path: 'tasks',      name: 'Tasks',     component: () => import('@/views/Tasks.vue'),     meta: { title: '任务管理' } },
      { path: 'settings',   name: 'Settings',  component: () => import('@/views/Settings.vue'),  meta: { title: '系统设置' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  NProgress.start()
  document.title = `${to.meta.title || '管理后台'} - 抖音达人联络官`
})
router.afterEach(() => NProgress.done())

export default router
