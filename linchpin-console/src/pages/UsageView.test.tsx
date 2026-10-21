import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import UsageView from './UsageView';
import { apiClient } from '../api/client';
import type { UsageResponse } from '../types';

vi.mock('../api/client', () => ({
  apiClient: { get: vi.fn() },
}));

const mockedGet = vi.mocked(apiClient.get);

const dayUsage: UsageResponse = {
  group_by: 'day',
  from: '2026-01-01T00:00:00Z',
  to: '2026-01-02T23:59:59Z',
  total: {
    bucket: 'total',
    sessions: 12,
    input_tokens: 1200,
    output_tokens: 340,
    cache_creation_input_tokens: 56,
    cache_read_input_tokens: 78,
  },
  buckets: [
    {
      bucket: '2026-01-01',
      sessions: 5,
      input_tokens: 800,
      output_tokens: 200,
      cache_creation_input_tokens: 30,
      cache_read_input_tokens: 40,
    },
    {
      bucket: '2026-01-02',
      sessions: 7,
      input_tokens: 400,
      output_tokens: 140,
      cache_creation_input_tokens: 26,
      cache_read_input_tokens: 38,
    },
  ],
};

const projectUsage: UsageResponse = {
  group_by: 'project',
  from: '2026-01-01T00:00:00Z',
  to: '2026-01-02T23:59:59Z',
  total: dayUsage.total,
  buckets: [
    {
      bucket: 'proj-alpha',
      sessions: 8,
      input_tokens: 900,
      output_tokens: 220,
      cache_creation_input_tokens: 40,
      cache_read_input_tokens: 50,
    },
    {
      bucket: 'proj-beta',
      sessions: 4,
      input_tokens: 300,
      output_tokens: 120,
      cache_creation_input_tokens: 16,
      cache_read_input_tokens: 28,
    },
  ],
};

describe('UsageView', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('renders KPI totals from the usage API', async () => {
    mockedGet.mockResolvedValue(dayUsage);
    render(<UsageView />);

    await waitFor(() => {
      expect(screen.getByTestId('usage-kpi-sessions')).toHaveTextContent('12');
    });
    expect(screen.getByTestId('usage-kpi-input')).toHaveTextContent('1.2k');
    expect(screen.getByTestId('usage-kpi-output')).toHaveTextContent('340');
    expect(screen.getByTestId('usage-kpi-cache-write')).toHaveTextContent('56');
    expect(screen.getByTestId('usage-kpi-cache-read')).toHaveTextContent('78');
  });

  it('requests group_by=day with ISO date bounds by default', async () => {
    mockedGet.mockResolvedValue(dayUsage);
    render(<UsageView />);

    await waitFor(() => expect(mockedGet).toHaveBeenCalled());
    const [path, params] = mockedGet.mock.calls[0];
    expect(path).toBe('/v1/usage');
    expect(params?.group_by).toBe('day');
    expect(params?.from).toMatch(/T00:00:00Z$/);
    expect(params?.to).toMatch(/T23:59:59Z$/);
  });

  it('renders buckets in the chart and breakdown table', async () => {
    mockedGet.mockResolvedValue(dayUsage);
    render(<UsageView />);

    await waitFor(() => {
      expect(screen.getByTestId('usage-chart')).toBeInTheDocument();
    });
    expect(screen.getByTestId('usage-table')).toHaveTextContent('2026-01-01');
    expect(screen.getAllByTestId('usage-row')).toHaveLength(2);
  });

  it('switches to project grouping and refetches', async () => {
    mockedGet.mockResolvedValueOnce(dayUsage).mockResolvedValueOnce(projectUsage);
    render(<UsageView />);

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('usage-group-project'));

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    expect(mockedGet.mock.calls[1][1]?.group_by).toBe('project');
    await waitFor(() => {
      expect(screen.getByTestId('usage-table')).toHaveTextContent('proj-alpha');
    });
  });

  it('applies a quick range preset', async () => {
    mockedGet.mockResolvedValue(dayUsage);
    render(<UsageView />);

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('usage-range-7'));

    await waitFor(() => expect(mockedGet).toHaveBeenCalledTimes(2));
    const [, params] = mockedGet.mock.calls[1];
    expect(params?.from).toMatch(/T00:00:00Z$/);
    expect(params?.to).toMatch(/T23:59:59Z$/);
  });

  it('shows the error state when the API fails', async () => {
    mockedGet.mockRejectedValue({
      status: 500,
      error: 'internal_error',
      message: 'boom',
    });
    render(<UsageView />);

    await waitFor(() => {
      expect(screen.getByTestId('error-message')).toBeInTheDocument();
    });
    expect(screen.getByTestId('error-text')).toHaveTextContent('boom');
  });

  it('shows the empty state when no usage is recorded', async () => {
    mockedGet.mockResolvedValue({ ...dayUsage, buckets: [] });
    render(<UsageView />);

    await waitFor(() => {
      expect(screen.getByTestId('usage-empty')).toBeInTheDocument();
    });
  });
});
