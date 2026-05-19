"use client"

import { useEffect, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { deleteUploadedFile } from "@/lib/file-storage";
import { toast } from "sonner";

// Datos de ejemplo para la tabla (puedes reemplazar esto con una carga real de datos en el futuro)
const sampleData = [
    { fecha: "2024-01-01", id: "PR01", nombre: "Zapatillas", categoria: "Deportes", cantidad: 275, venta: 49.77 },
    { fecha: "2024-01-02", id: "PR02", nombre: "Camisas", categoria: "Ropa", cantidad: 78, venta: 141.55 },
];

export default function DataDetailsPage() {
  const router = useRouter();
  const params = useParams<{ filename: string }>();
  const [fileName, setFileName] = useState("");

  useEffect(() => {
    if (params.filename) {
      setFileName(decodeURIComponent(params.filename));
    }
  }, [params]);

  const handleDeleteFile = () => {
    if (window.confirm(`¿Estás seguro de que deseas eliminar el archivo "${fileName}"?`)) {
      deleteUploadedFile(fileName);
      toast.success("Archivo eliminado correctamente");
      router.push('/cargar-datos');
    }
  };
  
  const handleIniciarChat = () => {
    router.push(`/?file=${encodeURIComponent(fileName)}`);
  };

  const isExcel = fileName.endsWith(".xlsx") || fileName.endsWith(".xls");

  return (
    <div className="flex h-screen bg-background">
      <Sidebar />
      <main className="flex-1 flex flex-col">
        {/* Encabezado superior simplificado */}
        <header className="border-b border-border px-6 py-4">
          <div className="flex items-center justify-between">
            <h1 className="text-lg font-semibold text-foreground">Dashboard de Ventas Q3</h1>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-blue-600 rounded-full flex items-center justify-center text-white text-sm font-medium">
                LB
              </div>
            </div>
          </div>
        </header>

        <div className="flex-1 p-6 overflow-auto">
          <div className="max-w-6xl mx-auto">
            {/* --- INICIO DE LA SECCIÓN MODIFICADA --- */}
            <Card className="p-6">
              <CardHeader className="p-0 mb-6 flex flex-col gap-4">
                {/* Fila superior del encabezado */}
                <div className="flex justify-between items-center gap-150">
                  <CardTitle className="text-3xl font-semibold">Datos del Archivo</CardTitle>
                  <div className="flex items-center gap-2">
                    <span className="text-sm text-muted-foreground">Mostrando {sampleData.length} registros</span>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-red-600 border-red-600 hover:bg-red-50 hover:text-red-700"
                      onClick={handleDeleteFile}
                    >
                      Eliminar
                    </Button>
                  </div>
                </div>
                {/* Fila inferior del encabezado */}
                <div className="flex justify-start items-center border-t border-border pt-4">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    <img 
                      src={isExcel ? "/Excel.svg" : "/CSV.svg"} 
                      alt="Ícono de archivo" 
                      className="h-5 w-5" 
                    />
                    <span>{fileName}</span>
                  </div>
                  <Button size="sm" onClick={handleIniciarChat} className="ml-8">
                    Iniciar chat
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <div className="overflow-x-auto border rounded-lg">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border">
                        <th className="text-left py-3 px-4 font-medium text-muted-foreground">Fecha</th>
                        <th className="text-left py-3 px-4 font-medium text-muted-foreground">ID</th>
                        <th className="text-left py-3 px-4 font-medium text-muted-foreground">Nombre</th>
                        <th className="text-left py-3 px-4 font-medium text-muted-foreground">Categoría</th>
                        <th className="text-right py-3 px-4 font-medium text-muted-foreground">Cantidad vendida</th>
                        <th className="text-right py-3 px-4 font-medium text-muted-foreground">Total venta PEN</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sampleData.map((row, index) => (
                        <tr key={index} className="border-b border-border/50 last:border-b-0 hover:bg-muted/50">
                          <td className="py-3 px-4">{row.fecha}</td>
                          <td className="py-3 px-4 font-medium">{row.id}</td>
                          <td className="py-3 px-4">{row.nombre}</td>
                          <td className="py-3 px-4">
                            <span className="inline-flex items-center px-2 py-1 rounded-full text-xs bg-muted text-muted-foreground">
                              {row.categoria}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-right font-medium">{row.cantidad}</td>
                          <td className="py-3 px-4 text-right font-medium">{row.venta}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
             {/* --- FIN DE LA SECCIÓN MODIFICADA --- */}
          </div>
        </div>
      </main>
    </div>
  )
}