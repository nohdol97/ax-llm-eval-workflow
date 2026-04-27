"use client";

/**
 * 비교 장바구니 (Compare Basket) Context.
 *
 * - Run name 또는 experiment id를 최대 5개까지 담아두는 클라이언트 상태.
 * - localStorage(`ax:compare_basket`)에 영속화하여 새 탭/리프레시에서도 유지.
 * - 다른 탭과 동기화: `storage` 이벤트 수신.
 *
 * 페이지/컴포넌트는 `useBasket()` 훅을 통해서만 접근한다.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

const STORAGE_KEY = "ax:compare_basket";
export const BASKET_MAX = 5;

interface BasketState {
  /** 현재 담긴 항목 (정렬 보장 없음) */
  items: string[];
  /** id가 담겨 있는지 */
  has: (id: string) => boolean;
  /** id를 추가 (이미 있으면 무시, 최대 BASKET_MAX) */
  add: (id: string) => boolean;
  /** id를 제거 */
  remove: (id: string) => void;
  /** 전체 비우기 */
  clear: () => void;
  /** 토글: 있으면 제거, 없으면 추가 */
  toggle: (id: string) => boolean;
  /** 현재 담긴 개수 */
  count: number;
  /** 한도 도달 여부 */
  isFull: boolean;
}

const BasketContext = createContext<BasketState | null>(null);

function readStorage(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === "string").slice(0, BASKET_MAX);
  } catch {
    return [];
  }
}

function writeStorage(items: string[]) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    // ignore quota errors
  }
}

export function BasketProvider({ children }: { children: ReactNode }) {
  // SSR-safe 초기화: 서버에서는 빈 배열로 시작
  const [items, setItems] = useState<string[]>([]);

  // 마운트 시 localStorage 로드
  useEffect(() => {
    setItems(readStorage());
  }, []);

  // 다른 탭과 동기화
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      setItems(readStorage());
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const persist = useCallback((next: string[]) => {
    setItems(next);
    writeStorage(next);
  }, []);

  const has = useCallback((id: string) => items.includes(id), [items]);

  const add = useCallback(
    (id: string) => {
      if (!id) return false;
      if (items.includes(id)) return true;
      if (items.length >= BASKET_MAX) return false;
      persist([...items, id]);
      return true;
    },
    [items, persist]
  );

  const remove = useCallback(
    (id: string) => {
      if (!items.includes(id)) return;
      persist(items.filter((x) => x !== id));
    },
    [items, persist]
  );

  const clear = useCallback(() => {
    if (items.length === 0) return;
    persist([]);
  }, [items.length, persist]);

  const toggle = useCallback(
    (id: string) => {
      if (items.includes(id)) {
        persist(items.filter((x) => x !== id));
        return false;
      }
      if (items.length >= BASKET_MAX) return false;
      persist([...items, id]);
      return true;
    },
    [items, persist]
  );

  const value = useMemo<BasketState>(
    () => ({
      items,
      has,
      add,
      remove,
      clear,
      toggle,
      count: items.length,
      isFull: items.length >= BASKET_MAX,
    }),
    [items, has, add, remove, clear, toggle]
  );

  return <BasketContext.Provider value={value}>{children}</BasketContext.Provider>;
}

export function useBasket(): BasketState {
  const ctx = useContext(BasketContext);
  if (!ctx) {
    throw new Error("useBasket must be used within <BasketProvider>");
  }
  return ctx;
}
