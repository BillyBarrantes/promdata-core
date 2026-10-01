"use client"

import React, { useEffect, useState, useMemo } from "react"
import { useSupabase } from "@/lib/supabase-provider"

export function HeaderUserAvatar() {
  const supabase = useSupabase()
  const [user, setUser] = useState<any>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    let isMounted = true
    setMounted(true)

    const fetchUser = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession()
        if (isMounted) {
          setUser(session?.user ?? null)
        }
      } catch (err) {
        console.error("Error fetching session for header avatar:", err)
      }
    }

    void fetchUser()

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event: string, session: any) => {
      if (!isMounted) return
      setUser(session?.user ?? null)
    })

    return () => {
      isMounted = false
      subscription?.unsubscribe()
    }
  }, [supabase])

  const profileName = useMemo(() => {
    const candidates = [
      user?.user_metadata?.full_name,
      user?.user_metadata?.name,
      user?.user_metadata?.preferred_username,
      user?.email?.split("@")[0],
    ]
    for (const candidate of candidates) {
      if (typeof candidate === "string" && candidate.trim()) {
        return candidate.trim()
      }
    }
    return ""
  }, [user])

  const profileInitials = useMemo(() => {
    if (!profileName) return "U"
    const parts = profileName.split(/\s+/).filter(Boolean).slice(0, 2)
    return parts.map((part: string) => part.charAt(0).toUpperCase()).join("") || "U"
  }, [profileName])

  if (!mounted) {
    return <div className="w-8 h-8 rounded-full bg-muted/60 animate-pulse shrink-0" />
  }

  return (
    <div
      className="w-8 h-8 bg-accent rounded-full flex items-center justify-center text-accent-foreground text-xs font-semibold shrink-0 shadow-sm select-none transition-all duration-200 ease-[cubic-bezier(0.2,0,0,1)] hover:scale-105 hover:shadow-md cursor-default"
      title={profileName || user?.email || "Usuario"}
      aria-label={profileName || user?.email || "Usuario"}
    >
      {profileInitials}
    </div>
  )
}
