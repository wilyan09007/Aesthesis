import { prisma } from "@/lib/prisma"
import type { Prisma } from "@prisma/client"
import type { AnalyzeResponse, ROIValues } from "@/lib/types"
import { ROI_KEYS } from "@/lib/types"
import { framesFromTimeline } from "@/lib/adapt"

export async function upsertUser(auth0Id: string, email: string, name?: string | null) {
  return prisma.user.upsert({
    where: { auth0Id },
    update: { email, name: name ?? null },
    create: { auth0Id, email, name: name ?? null },
  })
}

export async function createRun(
  userId: string,
  data: { goal?: string | null; urlA?: string | null; urlB?: string | null }
) {
  return prisma.run.create({
    data: { userId, goal: data.goal ?? null, urlA: data.urlA ?? null, urlB: data.urlB ?? null },
    select: { id: true, status: true, createdAt: true },
  })
}

export async function listRuns(userId: string, limit = 20) {
  return prisma.run.findMany({
    where: { userId },
    orderBy: { createdAt: "desc" },
    take: limit,
    select: {
      id: true,
      status: true,
      goal: true,
      urlA: true,
      urlB: true,
      createdAt: true,
      updatedAt: true,
      summary: { select: { winner: true, summaryText: true } },
    },
  })
}

export type RunListItem = Awaited<ReturnType<typeof listRuns>>[number]

export async function getRunForUser(runId: string, userId: string) {
  return prisma.run.findFirst({
    where: { id: runId, userId },
    include: {
      summary: true,
      insights: { orderBy: { timestampStart: "asc" } },
      versions: true,
    },
  })
}

function computeAvgRoi(values: ROIValues[]): ROIValues {
  if (!values.length) return Object.fromEntries(ROI_KEYS.map((k) => [k, 0])) as ROIValues
  const sums = Object.fromEntries(ROI_KEYS.map((k) => [k, 0])) as Record<keyof ROIValues, number>
  for (const v of values) {
    for (const key of ROI_KEYS) sums[key] += v[key]
  }
  return Object.fromEntries(ROI_KEYS.map((k) => [k, sums[k] / values.length])) as ROIValues
}

export async function saveRunSummary(runId: string, userId: string, data: AnalyzeResponse) {
  const run = await prisma.run.findFirst({ where: { id: runId, userId }, select: { id: true } })
  if (!run) return null

  const frames = framesFromTimeline(data.timeline)
  const avgRoiA = computeAvgRoi(frames.map((f) => f.values))
  const summaryText = data.overall_assessment.summary_paragraph

  return prisma.$transaction(async (tx: Prisma.TransactionClient) => {
    const summary = await tx.runSummary.upsert({
      where: { runId },
      update: { winner: "single", summaryText, avgRoiA, avgRoiB: {} },
      create: { runId, winner: "single", summaryText, avgRoiA, avgRoiB: {} },
    })

    await tx.runInsight.deleteMany({ where: { runId } })

    if (data.insights.length > 0) {
      await tx.runInsight.createMany({
        data: data.insights.map((ins) => ({
          runId,
          version: "A",
          timestampStart: ins.timestamp_range_s[0],
          timestampEnd: ins.timestamp_range_s[1],
          uxObservation: ins.ux_observation,
          recommendation: ins.recommendation,
        })),
      })
    }

    await tx.run.update({ where: { id: runId }, data: { status: "COMPLETE" } })

    return summary
  })
}

export type CompareResult = {
  current: RunSummaryShape
  past: RunSummaryShape
  delta: Record<keyof ROIValues, { a: number; b: number }>
}

export type RunSummaryShape = {
  runId: string
  winner: string
  summaryText: string
  avgRoiA: ROIValues
  avgRoiB: ROIValues
  createdAt: string
  goal: string | null
}

export async function compareRuns(
  currentRunId: string,
  pastRunId: string,
  userId: string
): Promise<CompareResult | null> {
  const [currentRun, pastRun] = await Promise.all([
    prisma.run.findFirst({ where: { id: currentRunId, userId }, include: { summary: true } }),
    prisma.run.findFirst({ where: { id: pastRunId, userId }, include: { summary: true } }),
  ])

  if (!currentRun?.summary || !pastRun?.summary) return null

  const cA = currentRun.summary.avgRoiA as unknown as ROIValues
  const cB = currentRun.summary.avgRoiB as unknown as ROIValues
  const pA = pastRun.summary.avgRoiA as unknown as ROIValues
  const pB = pastRun.summary.avgRoiB as unknown as ROIValues

  const delta = Object.fromEntries(
    ROI_KEYS.map((k) => [k, { a: cA[k] - pA[k], b: cB[k] - pB[k] }])
  ) as Record<keyof ROIValues, { a: number; b: number }>

  return {
    current: {
      runId: currentRun.id,
      winner: currentRun.summary.winner,
      summaryText: currentRun.summary.summaryText,
      avgRoiA: cA,
      avgRoiB: cB,
      createdAt: currentRun.createdAt.toISOString(),
      goal: currentRun.goal,
    },
    past: {
      runId: pastRun.id,
      winner: pastRun.summary.winner,
      summaryText: pastRun.summary.summaryText,
      avgRoiA: pA,
      avgRoiB: pB,
      createdAt: pastRun.createdAt.toISOString(),
      goal: pastRun.goal,
    },
    delta,
  }
}
