export interface MemoryFactLike {
  topic: string
  text: string
  added?: string
  score?: number
}

export const TOPIC_RE: RegExp
export const WRITE_TOOLS: string[]
export const DISTILL_PROMPT: string

export function norm(text: string): string
export function bulletText(text: string): string
export function isEcho(query: string, fact: string): boolean
export function formatMemory(facts: MemoryFactLike[]): string
export function messageText(msg: any): string
export function transcript(messages: any[]): string
export function parseFacts(raw: string, fallbackTopic: string): MemoryFactLike[]
export function redact(text: string): { text: string; count: number }
