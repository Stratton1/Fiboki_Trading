import useSWR from "swr";
import { api } from "@/lib/api";
import type { ChartDrawing, DrawingCreate, DrawingUpdate } from "@/types/contracts/drawings";

export function useDrawings(instrument: string, timeframe: string) {
  const key =
    instrument && timeframe ? `/drawings?instrument=${instrument}&timeframe=${timeframe}` : null;

  const { data, error, isLoading, mutate } = useSWR<ChartDrawing[]>(
    key,
    () => api.listDrawings(instrument, timeframe),
    {
      revalidateOnFocus: false,
      dedupingInterval: 10_000,
    }
  );

  const createDrawing = async (body: DrawingCreate) => {
    const created = await api.createDrawing(body);
    await mutate();
    return created;
  };

  const updateDrawing = async (id: number, body: DrawingUpdate) => {
    const updated = await api.updateDrawing(id, body);
    await mutate();
    return updated;
  };

  const deleteDrawing = async (id: number) => {
    await api.deleteDrawing(id);
    await mutate();
  };

  const clearDrawings = async () => {
    await api.clearDrawings(instrument, timeframe);
    await mutate();
  };

  return {
    drawings: data ?? [],
    error: error as Error | null,
    isLoading,
    createDrawing,
    updateDrawing,
    deleteDrawing,
    clearDrawings,
  };
}
