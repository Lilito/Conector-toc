# -*- coding: utf-8 -*-

'''
Created on 12/12/2011

@author: Erich Cordoba
'''

from tarjetaTOC import tarjetaTOC
from conectorBD import conectorBD
from conector_base_remota import conector_remoto
from simulador_tarjeta import simulador
from validar import validador
from alarmas_email import alarmas_email
from threading import Thread
from threading import Event
from gui import gui
from datetime import datetime
from ConfigParser import SafeConfigParser
import logging
import logging.handlers

import Queue
import gobject
import gtk
import time
import os

class adquisicion_toc():
    ''' Clase que funciona como enlace entre la tarjeta de adquisicion de datos TOC
    y el servidor MySQL. Funciona con una interfaz grafica desarrollada en Glade '''
    def __init__(self):
        #gobject.GObject.__init__(self)
        #gobject.threads_init()
        gtk.gdk.threads_init()
        # Para la simulacion, llamar al procedimiento simular cada segundo.
        #gobject.timeout_add(1000, self.simular)

        # Definiciones generales

        self.ALARMA = False
        self.DEBUG  = True
        self.modulos_activos = []
        self.pila_sql = Queue.Queue(maxsize=100)
        self.pila_tramas_leidas = Queue.Queue()

        self.logs = logging.getLogger("logtoc")
        self.logs.setLevel(logging.DEBUG)
        path_logs = os.path.join(os.path.dirname(__file__), "toc.log")
        self.logs_h = logging.FileHandler(path_logs)
        self.logs_f = logging.Formatter("%(name)s: %(levelname)s %(asctime)s %(module)s:%(funcName) %(lineno)d: %(message)s")
        self.logs_h.setFormatter(self.logs_f)
        self.logs_h.setLevel(logging.DEBUG)
        self.logs.addHandler(self.logs_h)


        self.validar = validador(self)
        self.alarmas = alarmas_email(self)
        # Obtener configuracion del archivo.

        self.configuracion = SafeConfigParser()
        self.cargar_configuracion()
        self.id_localizacion = self.configuracion.get("lugar", "id")

        self.base = conectorBD(self)

        self.ventana = gui(self)


        self.tarjeta = tarjetaTOC(self)

        if self.base.conectar():
            self.ventana.cambiar_estado_base("Conectada")
        else:
            self.ventana.cambiar_estado_base("Falló")

        # Comentar esta linea cuando ya no sea necesaria la simulacion
        #self.simular()
        self.guardar_en_pila(self.guardar_evento("Se inicio la aplicación", "0"))
        self.logs.debug("Se inicio la aplicacion")

    def main(self):
        self.despachador_hilos()
        gtk.main()

    def cargar_configuracion(self):
        ''' Lee la configuración del archivo configuracion.cfg. '''
        try:
            path_conf = os.path.join(os.path.dirname(__file__), "configuracion.cfg")
            self.configuracion.read(path_conf)
            for opcion in self.configuracion.options("modulos"):
                self.modulos_activos.append([opcion, bool(self.configuracion.get("modulos", opcion))])
        except:
            print "No se encuentra el archivo de configuracion"
            self.logs.exception("No se encontro el archivo de configuracion")

    def despachador_hilos(self):
    	
        self.evento_alarma = Event()
        self.evento_leer = Event()
        self.evento_escritura = Event()
        self.evento_sql = Event()
        self.evento_tramas = Event()

        self.h_alarma = Thread(target=self.hilo_alarmas, args=(self.evento_alarma,))
        self.h_lectura = Thread(target=self.hilo_lectura, args=(self.evento_leer,))
        self.h_escritura = Thread(target=self.hilo_escritura, args=(self.evento_escritura,))
        self.h_actualizacion_bd = Thread(target=self.hilo_actualizacion_bd, args=(self.evento_sql,))
        self.h_tramas = Thread(target=self.hilo_analisis_tramas, args=(self.evento_tramas,))

        self.h_escritura.start()
        self.h_lectura.start()
        self.h_actualizacion_bd.start()
        self.h_alarma.start()
        self.h_tramas.start()

    def hilo_escritura(self, evento):
        pass

    def guardar_evento(self, evento, modulo):
        fecha = datetime.now()
        return self.base.insertar_evento(fecha, modulo, evento)


    def hilo_actualizacion_bd(self, evento):

        conectado = False
        conector = conector_remoto(self)
        salir = False
        while(not evento.is_set()):
            if not conectado:
                try:
                    conectado = conector.conectar()
                    salir = False
                except:
                    self.logs.exception("No se conecto al servidor remoto de base de datos")
                    gobjecto.idle_add(self.ventana.cambiar_estado_base_remota, "Falló")
                    conectado = False
            if conectado:
                if not self.pila_sql.empty(): # Si la pila tiene elementos
                    while(not self.pila_sql.empty() and not salir):
                        try:
                            if conector.ejecutar_comando(self.pila_sql.get()):
                                conectado = True
                                gobject.idle_add(self.ventana.cambiar_estado_base_remota, "Detectada")
                            else:
                                conectado = False
                                gobject.idle_add(self.ventana.cambiar_estado_base_remota, "Falló")
                                salir = True
                        except:
                            self.logs.exception("No fue posible ejecutar sentencia en base de datos.")
                            conectado = False
                            gobject.idle_add(self.ventana.cambiar_estado_base_remota, "Falló")
                        if self.DEBUG:
                            print "DEBUG: SQL Ejecutado en servidor remoto"
                        if self.DEBUG:
                            print "DEBUG: %s"  % (self.pila_sql.get())
            time.sleep(15)
        if conectado:
            conector.desconectar()
        if self.DEBUG:
            self.logs.debug("Saliendo del hilo de actualizacion BD")
            print "DEBUG: Saliendo del hilo actualizacion BD"


    def hilo_lectura(self, evento):
        time.sleep(1)
        self.tarjeta.abrir_puerto()
        while(not evento.is_set()):

            recv = self.tarjeta.leer_datos()
            if (recv):
                # Actualizar trama en la pila de tramas
                gobject.idle_add(self.ventana.cambiar_estado_tarjeta, "Detectada")
                self.pila_tramas_leidas.put(recv)
                if self.DEBUG:
                    #self.logs.debug(recv)
                    print "DEBUG TRAMA RECV: %s" % (recv)
        #gtk.gdk.threads_leave()
        if self.DEBUG:
            print "DEBUG: Se ha terminado el hilo lectura"
        self.tarjeta.cerrar_puerto()

    def hilo_analisis_tramas(self, evento):
        time.sleep(1)
        while(not evento.is_set()):
            if (not self.pila_tramas_leidas.empty()):
                trama = self.pila_tramas_leidas.get()
                gobject.idle_add(self.analizar_trama, trama)
            time.sleep(0.5)
        if self.DEBUG:
            print "DEBUG: Se ha terminado el hilo tramas"

    def hilo_alarmas(self, evento):
        while(not evento.is_set()):
            if self.ALARMA:
                os.system("beep -f 500 -l 500 -n -f 400 -l 500 -n -f 500 -l 500 -n -f 400 -l 500")
            time.sleep(2)
        if self.DEBUG:
            print "DEBUG: Se ha terminado el hilo alarmas"

    def cerrar_programa(self):
        ''' Procedimiento para cerrar el programa '''
        self.guardar_evento("Se cerró el programa", "0")
        self.base.desconectar()
        self.tarjeta.desconectar()
        self.evento_alarma.set()
        self.evento_leer.set()
        self.evento_sql.set()
        self.evento_tramas.set()
        if self.DEBUG:
            print "DEBUG: Saliendo del programa, hilos terminados"
        gtk.main_quit()

    def simular(self):
        ''' Simulacion de recepcion de tramas desde la tarjeta '''
        datos = self.tarjeta.leer_tarjeta()
        if len(datos) > 0:
            self.ventana.cambiar_estado_tarjeta("Detectada")
            self.analizar_trama(datos)
        else:
            self.ventana.cambiar_estado_tarjeta("Falló")
            self.guardar_evento("Falló la deteción de la tarjeta", "0")
        return True

    def guardar_en_pila(self, sql):
        if not self.pila_sql.full():
            self.pila_sql.put(sql)


    def analizar_trama(self, trama):
        ''' Metodo que recibe una trama y analiza su contenido
        actualiza la base de datos y el modelo dentro de la interfaz web.
        Separa las tramas utilizando las comas como separador generando un arreglo con
        el resultado. '''

        sql = ""
        fecha = datetime.now()
        bloques = trama.split(',')

        if bloques[1] == "01":
            if self.validar.validar_biodigestor_metano(bloques):
                sql = self.base.insertar_biodigestor_metano(fecha,
                                                          bloques[2],
                                                          bloques[3],
                                                          bloques[4],
                                                          bloques[5],
                                                          bloques[6],
                                                          bloques[7],
                                                          bloques[8],
                                                          bloques[9],
                                                          bloques[10])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Biodigestor metano : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
                print "ERROR TRAMA : %s" % (trama)
            if self.DEBUG:
                print "DEBUG: %s" % (sql)

        elif bloques[1] == "02":
            if self.validar.validar_torre_bioetanol(bloques):
                sql = self.base.insertar_torre_bioetanol(fecha,
                	                                       bloques[2],
                	                                       bloques[3],
                	                                       bloques[4],
                	                                       bloques[5],
                	                                       bloques[6],
                	                                       bloques[7],
                	                                       bloques[8],
                	                                       bloques[9])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Torre de bioetanol : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
                print "ERROR TRAMA : %s" % (trama)
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "03":
            if self.validar.validar_reactor_biodiesel(bloques):
                sql = self.base.insertar_reactor_biodiesel(fecha,
                                                    bloques[2],
                                                    bloques[3],
                                                    bloques[4],
                                                    bloques[5],
                                                    bloques[6],
                                                    bloques[7],
                                                    bloques[8],
                                                    bloques[9],
                                                    bloques[10],
                                                    bloques[11])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Reactor biodiesel : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "04":
            if self.validar.validar_calentador_solar(bloques):
                sql = self.base.insertar_calentador_solar(fecha,
                                                    bloques[2],
                                                    bloques[3],
                                                    bloques[4],
                                                    bloques[5])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Calentador solar : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "05":
            if self.validar.validar_generador_eolico(bloques):
                sql = self.base.insertar_generador_eolico(fecha,
                                                    bloques[2],
                                                    bloques[3],
                                                    bloques[4],
                                                    bloques[5])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Generador eolico : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "06":
            if self.validar.validar_generador_magnetico(bloques):
                sql = self.base.insertar_generador_magnetico(fecha,
                                                       bloques[2],
                                                       bloques[3],
                                                       bloques[4])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Generador magnetico : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "07":
            if self.validar.validar_generador_stirling(bloques):
                sql = self.base.insertar_generador_calentador_stirling(fecha,
                                                                 bloques[2],
                                                                 bloques[3],
                                                                 bloques[4],
                                                                 bloques[5])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Calentador stirling : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "08":
            if self.validar.validar_bomba_stirling(bloques):
                sql = self.base.insertar_bomba_de_agua(fecha,
                                                       bloques[2],
                                                       bloques[3],
                                                       bloques[4])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Bomba de Agua : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)

        elif bloques[1] == "09":
            if self.validar.validar_lombricomposta(bloques):
                sql = self.base.insertar_lombricompostario(fecha,
                                                     bloques[2],
                                                     bloques[3],
                                                     bloques[4],
                                                     bloques[5])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Lombricomposta : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)

        elif bloques[1] == "10":
            if self.validar.validar_acuaponia(bloques):
                sql = self.base.insertar_acuaponia(fecha,
                                             bloques[2],
                                             bloques[3],
                                             bloques[4],
                                             bloques[5],
                                             bloques[6],
                                             bloques[7],
                                             bloques[8],
                                             bloques[9],
                                             bloques[10],
                                             bloques[11],
                                             bloques[12],
                                             bloques[13],
                                             bloques[14],
                                             bloques[15],
                                             bloques[16])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Acuaponia : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "11":
            if self.validar.validar_destilador_solar(bloques):
                sql = self.base.insertar_destilador_solar(fecha,
                                                    bloques[2],
                                                    bloques[3],
                                                    bloques[4],
                                                    bloques[5])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Destilador solar : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "12":
            if self.validar.validar_condensador_atmosferico(bloques):
                sql = self.base.insertar_condensador_atmosferico(fecha,
                                                           bloques[2],
                                                           bloques[3],
                                                           bloques[4],
                                                           bloques[5],
                                                           bloques[6],
                                                           bloques[7],
                                                           bloques[8],
                                                           bloques[9])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Condensador atmosferico : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:	
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "13":
            if self.validar.validar_agua_de_lluvia(bloques):
                sql = self.base.insertar_agua_de_lluvia(fecha,
                                                  bloques[2],
                                                  bloques[3],
                                                  bloques[4],
                                                  bloques[5],
                                                  bloques[6],
                                                  bloques[7],
                                                  bloques[8],
                                                  bloques[9])
                self.ventana.actualizar_modelo(bloques)
                self.alarmas.revisar_alarma_agua_de_lluvia(bloques)
                self.ventana.cambiar_barra_estado("Agua de lluvia : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:	
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)
        elif bloques[1] == "14":
            if self.validar.validar_autonomia_transporte(bloques):
                sql = self.base.insertar_autonomia_transporte(fecha,
                                                        bloques[2])

                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Autonomia de transporte : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:	
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)

        elif bloques[1] == "15":
            if self.validar.validar_enfriamiento_adsorcion(bloques):
                sql = self.base.insertar_enfriamiento_adsorcion(fecha,
                                                          bloques[2],
                                                          bloques[3],
                                                          bloques[4],
                                                          bloques[5],
                                                          bloques[6],
                                                          bloques[7],
                                                          bloques[8])
                self.ventana.actualizar_modelo(bloques)
                self.ventana.cambiar_barra_estado("Enfriamiento por adsorcion : " + trama)
                self.ventana.cambiar_estado_actividad(fecha.strftime('%Y-%m-%d %H:%M:%S'))
                self.guardar_en_pila(sql)
            else:	
                cadena_evento = "Trama incorrecta : %s" % (trama)
                id_mod = str(int(bloques[1]))
                self.guardar_en_pila(self.guardar_evento(cadena_evento, id_mod))
            if self.DEBUG:
                print "DEBUG: %s" % (sql)

        else:
        	self.ventana.cambiar_barra_estado("Trama incorrecta : " + trama)
                if self.DEBUG:
                    print "DEBUG: Trama incorrecta %s" % (trama)



    def leer_tarjeta(self):
        self.tarjeta.abrir_puerto()
        while(True):
            dato = self.tarjeta.leer_datos()
            if len(dato) > 0:

                datos = dato.split(',')
                if datos[1] == '09':
                    print "Lombricomposta"
                    temp = float(datos[5])
                    print "Temperatura : %.2f" % temp
                    fecha = datetime.now()
                    self.base.insertar_lombricompostario(fecha,
                                                         datos[2],
                                                         datos[3],
                                                         datos[4],
                                                         datos[5].strip("\r\n"))


    def enviar_comando(self):
        try:
            self.tarjeta.abrir_puerto()
            comando = "CON,09,02,00,00,00,00,00,00"
            self.tarjeta.escribir_datos(comando)
            print self.tarjeta.leer_datos()
            self.tarjeta.cerrar_puerto()
            #self.tarjeta.serial.write('1')
            #time.sleep(0.1)
            #self.tarjeta.escribir_datos(comando)
        except:
            print "Error al abrir el puerto"

# CON,09,02,00,00,00,00,00,00

if __name__ == '__main__':
    adq = adquisicion_toc()
    adq.main()
    #time.sleep(3)
    #con.enviar_comando()
#    con.simular()
    #con.leer_tarjeta()
