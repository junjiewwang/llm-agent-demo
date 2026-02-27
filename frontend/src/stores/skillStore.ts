/**
 * Skill 管理 Store
 *
 * 管理 Skill 列表加载、启停切换等状态。
 * Skills 是全局资源，不区分租户。
 */
import { create } from 'zustand'
import type { SkillInfo } from '../types/index'
import { listSkills, toggleSkill } from '../api/client'
import { toast } from './toastStore'

interface SkillState {
  skills: SkillInfo[]
  loading: boolean
  error: string | null

  /** 从后端加载 Skill 列表 */
  fetchSkills: () => Promise<void>

  /** 切换指定 Skill 的启停状态 */
  toggle: (name: string, enabled: boolean) => Promise<void>
}

export const useSkillStore = create<SkillState>((set, get) => ({
  skills: [],
  loading: false,
  error: null,

  fetchSkills: async () => {
    set({ loading: true, error: null })
    try {
      const res = await listSkills()
      if (res.success) {
        set({ skills: res.data, loading: false })
      } else {
        const msg = res.error || '加载失败'
        set({ error: msg, loading: false })
        toast.error(msg)
      }
    } catch (e) {
      const msg = (e as Error).message
      set({ error: msg, loading: false })
      toast.error(msg)
    }
  },

  toggle: async (name, enabled) => {
    // 乐观更新：先更新 UI，失败再回滚
    const prev = get().skills
    const displayName = prev.find((s) => s.name === name)?.display_name || name
    set({
      skills: prev.map((s) => (s.name === name ? { ...s, enabled } : s)),
    })

    try {
      const res = await toggleSkill(name, enabled)
      if (res.success) {
        toast.success(`${displayName} 已${enabled ? '启用' : '禁用'}`)
      } else {
        set({ skills: prev })
        toast.error(res.error || '操作失败')
      }
    } catch (e) {
      set({ skills: prev })
      toast.error((e as Error).message)
    }
  },
}))
