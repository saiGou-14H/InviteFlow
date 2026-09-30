import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import UserWorkspace from './components/UserWorkspace.vue'
import AdminView from './views/AdminView.vue'
import NotFoundView from './views/NotFoundView.vue'
import './style.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: UserWorkspace },
    { path: '/admin', component: AdminView },
    { path: '/:pathMatch(.*)*', component: NotFoundView },
  ],
  scrollBehavior: () => ({ top: 0 }),
})
createApp(App).use(router).mount('#app')
