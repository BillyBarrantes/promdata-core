import { createClient } from "@/lib/supabase-client";
import { API_BASE_URL } from "@/lib/api-config";

const supabase = createClient();

export interface SavedReport {
    id: number;
    title: string;
    description?: string;
    type?: string;
    content: any;
    data?: any; // Compatibilidad legacy para reportes antiguos
    created_at?: string;
}

// Fetch all reports from Backend API
export const getSavedReports = async (): Promise<SavedReport[]> => {
    try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) return [];

        const response = await fetch(`${API_BASE_URL}/api/v1/reports`, {
            headers: { 'Authorization': `Bearer ${session.access_token}` }
        });

        if (!response.ok) return [];
        return await response.json();
    } catch (error) {
        console.error("Error fetching reports:", error);
        return [];
    }
};

// Get single report by ID
export const getReportById = async (id: number): Promise<SavedReport | null> => {
    try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) return null;

        const response = await fetch(`${API_BASE_URL}/api/v1/reports/${id}`, {
            headers: { 'Authorization': `Bearer ${session.access_token}` }
        });

        if (!response.ok) return null;
        return await response.json();
    } catch (error) {
        console.error(`Error fetching report ${id}:`, error);
        return null;
    }
};

// Delete report
export const deleteReport = async (id: number): Promise<boolean> => {
    try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) return false;

        const response = await fetch(`${API_BASE_URL}/api/v1/reports/${id}`, {
            method: 'DELETE',
            headers: { 'Authorization': `Bearer ${session.access_token}` }
        });

        return response.ok;
    } catch (error) {
        console.error(`Error deleting report ${id}:`, error);
        return false;
    }
};
