#!/usr/local/bin/python
# -*- coding: utf-8 -*-

import tty
import sys
import termios
import time
import os
import math
import logging
from threading import Thread, Lock
import skimage
import numpy as np
import cv2
from picamera import PiCamera
from picamera.array import PiRGBArray
import picamera.array
import RPi.GPIO as GPIO   
import BBTree as BBT 
import LineFollower as LF
import hasel
import ApuntadorFuego as AF
import random as rand

import firenetRGB
from tflearn.metrics import *
from tflearn.layers.core import *
from tflearn.layers.conv import *
from tflearn.layers.normalization import *
from tflearn.layers.estimator import regression


__author__  = "David Lucas (1458833), Pol Coma (1426607), Dani Romero (1459469), Enrique Fernández (1456537)"
__license__ = "GPL"
__date__    = "29/05/2019"


# Clase principal que engloba todo el proyecto

class FirefAIghter():

    def __init__(self,recognizer_size_x=25,recognizer_size_y=25,white_tol=0.0015,centroid_tol = 0.10, color_tol = 0.025):
        # Init motores
        self.in1 = 26
        self.in2 = 19
        self.in3 = 21
        self.in4 = 20
        self.ena = 12 
        self.enb = 13
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.in1,GPIO.OUT)
        GPIO.setup(self.in2,GPIO.OUT)
        GPIO.setup(self.in3,GPIO.OUT)
        GPIO.setup(self.in4,GPIO.OUT)
        GPIO.setup(self.ena,GPIO.OUT)
        GPIO.setup(self.enb,GPIO.OUT)
        GPIO.output(self.in1,GPIO.LOW)
        GPIO.output(self.in2,GPIO.LOW)
        GPIO.output(self.in3,GPIO.LOW)
        GPIO.output(self.in4,GPIO.LOW)
        self.potmotl = GPIO.PWM(self.ena,1000)  
        self.potmotr = GPIO.PWM(self.enb,1000)
        self.potmotl.start(100)
        self.potmotr.start(100)
        self.potmotln = 90
        self.potmotrn = 90
        # Servo

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(17, GPIO.OUT)
        self.servo = GPIO.PWM(17, 50)
        # Diodo

        GPIO.setup(22,GPIO.IN)
        #Init camera
        self.picamera = PiCamera()
        self.picamera.resolution = (640,480)
        self.arbol = BBT.Stack()
        self.isFire = None # variable global que indica quan es troba foc
        self.results = None
        self.mutex = Lock()
        self.recognizer_size_x = recognizer_size_x
        self.recognizer_size_y = recognizer_size_y
        self.white_tolerance = white_tol
        self.color_tolerance = color_tol
        self.motion_tolerance = centroid_tol
        self.num_white_zones = 0
        self.extinct = False
        self.colors = [[253, 255, 247],[251, 255, 250],[251, 255, 252],[255, 253, 254],[255, 253, 252],[255, 253, 250],[255, 254, 250],[255, 253, 250],[252, 254, 253],[252, 254, 253],[232,137,91],[254,254,252],[254,225,149],[253,255,206],[252,216,118],[246,147,68],[255,217,188],[236,150,89],[255,255,199],[255,249,227],[205,201,190],[255,254,250],[255,255,191],[255,254,197],[255,249,150],[255,255,251],[254,253,199]]
        self.actual_frame = None
       
        #Init firenet
        self.model = firenetRGB.firenet(160,160,False, 0, 0, 0, 0)
        self.model.load("../NeuralNet/model/fire/firemodel.tfl",weights_only=True)
        self.firenet_result = None
        
        #Main
        self._printConfig()
        self._main()
        
    def _printConfig(self):
        print (self.recognizer_size_x)
        print (self.recognizer_size_y)
        print (self.white_tolerance)
        print (self.motion_tolerance)
        print (self.color_tolerance)
    
    def _main(self):
        
        op = '-1'
        while op != '0':
            op = str(input('Introduce la opcion que quieres ejecutrar:\n1.- Ver imagen Seguidor.\n2.- Ir a una zona y apagar fuego.\n3.- Deteccion fego\n4.- Shoot Water\n'))
            if op == '1':
                self._lineFollower(op='image')
            elif op == '2':
                self._lineFollower(op='goto')
                self._HSLBlockRecognizerLMC()
            elif op =='3':
                self._HSLBlockRecognizerLMC()
            elif op == '4':
                self._shootWater()
               
    def _lineFollower(self,op='',zone=None):
        self.picamera.resolution = (426,240) #trabajaremos con una resolucion inferior para disminuir el tiempo de procesado
        rawCapture = PiRGBArray(self.picamera)
        self.picamera.start_preview()
        aux = None
        if op == 'image':
            image=True
            irFuego = False
        elif op == 'goto':
            numSectores = 2 #Numero de sectores que guarda
            image=False
            irFuego = True #si hay que ir a un sector
            sectorLlamas = 1 #a que sector ir
            CountIrLlama = 1 #contador auxiliar del camino al fuego
        
        if irFuego:
            # Inicializar arbol
            CountBif=0
            CountSec=0
            pilaBif = BBT.Stack()
            Sectores = BBT.Stack()
            root = BBT.Node(CountBif)
            aux = BBT.Node(1)
            aux = root
            for a in range(0,numSectores):  
                # BIFURCACION
                CountBif = CountBif + 1
                aux = aux.RecorrerMapa(CountBif)
                pilaBif.pushh(CountBif)
                # SECTOR
                CountBif = CountBif + 1
                aux.data = CountBif
                pilaBif.pushh(CountBif)
                auxSec = pilaBif.stack.copy()
                Sectores.pushh(auxSec)
                CountSec = CountSec + 1
                aux = aux.padre
                pilaBif.popp()                      
            root.PrintTree()
        #### Por cada sector creamos antes una bifurcacion
        
        for image in self.picamera.capture_continuous(rawCapture, format="bgr"):
            frame = image.array
            ## Capturar frame a frame
            bif=0 #si bifurcacion 1
            sec=0 #si sector 1
            image.truncate(0)
            
            ### Ir a un sector
            if irFuego:
                p=0
                crop_img,bif,sec,mappedCx = LF.seguirLinea(frame) #devuelve la imagen recortada, si es bifurcacion o sector o nada y lo separado que esta de la linea (de -100 a 100)
                if bif==1: # si encuentra bifurcacion entra
                    if Sectores.stack[sectorLlamas][CountIrLlama] == aux.left.data: #mira si en esta bifurcacion hay que ir a la izquierda
                        p=0
                        self._moveMotors(1,0,0,1,self.potmotl,self.potmotr,1) #sigue un poco recto 
                        time.delay(1)
                        self._moveMotors(0,0,0,1,p,self.potmotr,1) #ahora gira 90 grados a la izquierda
                        time.delay(1.3)
                        aux=aux.left
                    elif Sectores.stack[sectorLlamas][CountIrLlama] == aux.right.data: #mira si en esta bifurcacion hay que ir a la derecha
                        p=0
                        if mappedCx < 0: #si detecta que la linea se encuentra a su izquierda entra
                            p=self.potmotl*((100-abs(mappedCx))/100) #se asigna potencia al motor izquierdo en base a lo separado que este de la linea
                            self._moveMotors(1,0,0,1,p,self.potmotr,1)
                        elif mappedCx >= 0: #si detecta que la linea se encuentra a su derecha entra
                            p=self.potmotr*((100-abs(mappedCx))/100) #se asigna potencia al motor derecho en base a lo separado que este de la linea
                            self._moveMotors(1,0,0,1,self.potmotl,p,1)
                        aux=aux.right
                    CountIrLlama+=1
                elif sec==1:
                    self._moveMotors(0,0,0,0,p,self.potmotrn,1)
                    break #para los motores y entra en la parte de buscar fuego en el sector
                else:
                    if mappedCx < 0:#si detecta que la linea se encuentra a su izquierda entra
                        p=self.potmotln-((abs(mappedCx)))
                        print ("potencia motor l: ", p)
                        self._moveMotors(1,0,0,1,p,self.potmotrn,1)#se asigna potencia al motor izquierdo en base a lo separado que este de la linea
                    else:
                        p=self.potmotrn-((abs(mappedCx)))#si detecta que la linea se encuentra a su derecha entra
                        print ("potencia motor r: ", p)
                        self._moveMotors(1,0,0,1,self.potmotln,p,1)#se asigna potencia al motor derecho en base a lo separado que este de la linea

            ###
            
            ### Ver seguidor linea
            if image:
                crop_img,bif,sec,mappedCx = LF.seguirLinea(frame)
                cv2.imshow('frame',crop_img)
                cv2.waitKey(0)
        cv2.destroyAllWindows()
    
    def _moveMotors(self,in1,in2,in3,in4,ena,enb,t):
        #print ('moviendo motores')
        self.potmotl.ChangeDutyCycle(ena)
        self.potmotr.ChangeDutyCycle(enb)
        GPIO.output(self.in1,in1)
        GPIO.output(self.in2,in2)
        GPIO.output(self.in3,in3)
        GPIO.output(self.in4,in4)
       
    def _shootWater(self):
        PIN = 27
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(PIN,GPIO.OUT)
        GPIO.output(PIN,1)
        rawCapture = PiRGBArray(self.picamera)
        self.picamera.framerate = 10
        
        for image in self.picamera.capture_continuous(rawCapture, format="bgr"):
            if self.extinct:
                break
            
            cv2.imwrite('tmp_fuego.jpg',image.array)
            image.truncate(0)
            np_arr_rgb = cv2.imread('tmp_fuego.jpg',1)
            photo = hasel.rgb2hsl(np_arr_rgb)
            photo[:,:,:] = photo[:,:,:] * 255
            black = np.uint8(photo[:,:,2])
            nums, num_counter = np.unique(black[:,:], return_counts=True)
            freq_dic = dict(zip(nums,num_counter))
            try:
                if freq_dic[255] > (self.recognizer_size_y*self.recognizer_size_x) * self.white_tolerance:
                    #print ('Sigue habiendo fuego')
                    self.extinct = False
                else:
                    #print ('No hay fuego ya')
                    GPIO.output(PIN,0)
                    self.extinct = True       
            except KeyError:
                self.extinct = True
        
        GPIO.output(PIN,0)
        
    def _executeNeuralNetwork(self,np_arr_rgb):
        img_sz = 160
        im = cv2.resize(np_arr_rgb, (img_sz,img_sz), interpolation=cv2.INTER_CUBIC)
        image = np.ones((1,img_sz,img_sz,3), dtype=np.uint8)*255
        image[0,:,:,:]=im[:,:,:]
        # Executem la xx
        output = self.model.predict(image)[0]
        print('Resultat de la xarxa neuronal: '+str(output))
        #El que tenga el resultado mas proximo a 1 sera la clasificacion
        if np.argmax(output) == 1: 
            self.firenet_result = False
        else:
            self.firenet_result = True

    def _HSLBlockRecognizerLMC(self):

        #Preparacio de la camara
        self.picamera.resolution = (640,480)
        rawCapture = PiRGBArray(self.picamera)
        self.picamera.start_preview()
        self.picamera.framerate = 20
        threads = []

        # Analitzem imatges captades per la camera.
        for image in self.picamera.capture_continuous(rawCapture, format="bgr"):
            time.sleep(0.1)
            
            '''
            Aplicacio de l'algoritme LMC: Lightness Motion Color Detection que s'ha dissenyat per a la deteccio de foc i que s'ha desenvolupat per a les assignatures RLP 
            i Visió per computador. 
            
            #################################################################################################################
            #### Durant el codi trobaras comentaris explicant cada pas de l'algoritme i d'altres que poden ser d'interes. ###
            
            1.- Agafem una imatge objectiu i li canviem l'espai de color d'RGB a HSL. 
            -> Ens quedem amb el tercer canal L que esta representat en blancs i negres.
            '''

            cv2.imwrite('FRAME_ORIGEN.jpg',image.array)

            image.truncate(0)
    
            np_arr_rgb = cv2.imread('FRAME_ORIGEN.jpg',1)
            self.actual_frame = np_arr_rgb
            photo = hasel.rgb2hsl(np.uint8(np_arr_rgb))
            photo[:,:,:] = np.uint8(photo[:,:,:]) * 255
            black = np.uint8(photo[:,:,2])
           
            R = G = B = 255 
            size_x = 0
            size_y = 0
            segment = None
            ini_x = 9999999
            ini_y = 9999999
            fi_x = 0
            fi_y = 0
            cX = 0
            cY = 0                
            max_width = photo.shape[1]
            max_height = photo.shape[0]
            max_num_colors = 0
            num_colors = 0
            start = time.time()
            last_is_white = False
            possiblyFire = False
            num_white_zones = 0
            whites_pos_list = []
            
            '''
            2.- Recorrem sencera aquesta imatge en blocs de tamany = recognizer_size_x * recognizer_size_y.
            ''' 
            for x in range(int(max_height/self.recognizer_size_x)+1):
                for y in range(int(max_width/self.recognizer_size_y)+1):
                    '''
                    -> Si un bloc surt de la imatge, adaptem la mida del bloc que estem analitzant. 
                    '''
                    try:
                        if black[ x * self.recognizer_size_x : (x * self.recognizer_size_x) + self.recognizer_size_x, y * self.recognizer_size_y : (y * self.recognizer_size_y) + self.recognizer_size_y].shape != (max_height,max_width):
                            segment = np.zeros((photo[ x * self.recognizer_size_x : (x * self.recognizer_size_x) + self.recognizer_size_x, y * self.recognizer_size_y : (y * self.recognizer_size_y) + self.recognizer_size_y].shape[0], photo[ x * self.recognizer_size_x : (x * self.recognizer_size_x) + self.recognizer_size_x, y * self.recognizer_size_y : (y * self.recognizer_size_y) + self.recognizer_size_y].shape[1]),dtype=np.uint8)   
                        else:
                            segment = np.zeros((self.recognizer_size_x,self.recognizer_size_y),dtype=np.uint8)

                        segment[:,:] = black[ x * self.recognizer_size_x : (x * self.recognizer_size_x) + self.recognizer_size_x, y * self.recognizer_size_y : (y * self.recognizer_size_y) + self.recognizer_size_y]
                        size_x = segment.shape[0]
                        size_y = segment.shape[1]
                    except ValueError as e:
                        raise e

                    nums = None
                    num_counter = None
                    freq_dic = None

                    '''
                    3.- Fem un recompte de quans valors 255 (blanc) apareix en aquesta imatge. 
                    -> Apliquem una tolerancia per decidir si ho considerem soroll o no.
                    -> Si es un bloc que sembla d'interes, el guardem a una llista que emmagatzema els blocs amb 
                    les seves coordenades corresponents.
                    '''
                    nums, num_counter = np.unique(segment[:,:], return_counts=True)
                    freq_dic = dict(zip(nums,num_counter))

                    try:
                        if freq_dic[255]:
                            if freq_dic[255] >= ((size_y*size_x) * self.white_tolerance):
                                possiblyFire = True
                                if last_is_white:
                                    fi_x = (x*size_x) + size_x
                                    fi_y = (y*size_y) + size_y
                                else:
                                    if ini_x > (x*size_x):
                                        ini_x = x*self.recognizer_size_x
                                    if ini_y > (y*size_y):
                                        ini_y = y*self.recognizer_size_y
                                    whites_pos_list.append([ini_x,ini_y])
                                    fi_x = (x*self.recognizer_size_x) + size_x
                                    fi_y = (y*self.recognizer_size_y) + size_y
                                    
                                last_is_white = True
                            else:
                                pass            
                    except KeyError:
                        if last_is_white:
                            whites_pos_list[-1].append(fi_x)
                            whites_pos_list[-1].append(fi_y)
                            whites_pos_list[-1].append(num_colors)
                            num_colors = 0
                            num_white_zones += 1
                            ini_x = 999999
                            ini_y = 999999
                            fi_y = 0
                            fi_x = 0
                            last_is_white = False
                        else:
                            ini_x = 999999
                            ini_y = 999999
                            fi_y = 0
                            fi_x = 0
            
            
            if possiblyFire:
                PIN = 24
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(PIN,GPIO.OUT)
                GPIO.output(PIN,1)
                
                print ('Hi ha: ',num_white_zones,' objectes blancs')

                
                res = [None] * (num_white_zones + 1)
                self.results = [None for i in range(num_white_zones)]

                '''
                -> En aquest punt tenim l'opcio de seguir amb el LMC o aplicar previament abans de seguir l'execucio del LMC, el projecte
                desenvolupat per l'altre alumne a l'assignatura de Visió per Computador. Una xarxa neuronal.
                
                # Versio on primer executem la xarxa neuronal per sobre i despres la deteccio de foc LMC en temps real:
                ##############################################################################################
                #self.neural_net_th = Thread(target = self._executeNeuralNetwork, args =(self,np_arr_rgb))
                #self.neural_net_th.start()

                -> El join del neural_net, el fem dins del LMC per paral·lelitzar la network i el LMC.
                -> (EXTRA) EN FASE DE PROVA : Aplicacio duna funcio per millorar els blocs trobats. Enten quines zones son el mateix objecte de
                la imatge inicial i agrupa els blocs deixant-ne tants com focus de llum te la imatge inicial en l'espai de color HSL, pel que fa al
                canal L (Lluminositat):

                #whites_pos_list = self._getBetterZones(whites_pos_list)
                #num_new_zones = len(whites_pos_list)
                #num_items_position = num_new_zones
                '''
                
                
                self._executeNeuralNetwork(np_arr_rgb)
                
                if self.firenet_result:

                    self.num_white_zones = num_white_zones

                    for zone in range(num_white_zones):
                        try:
                            ini_x = whites_pos_list[zone][0]
                            ini_y = whites_pos_list[zone][1]
                            fi_x = whites_pos_list[zone][2]
                            fi_y = whites_pos_list[zone][3]
                            
                            '''
                            -> Mirem bloc per bloc i calculem el seu centre de massa blanca (centroide).
                            '''
                            segment = np.uint8(black[ini_x:fi_x,ini_y:fi_y])
                            M = cv2.moments(segment)
                            
                            try:
                                cY = int(M["m10"] / M["m00"])
                                cX = int(M["m01"] / M["m00"])
                                '''
                                -> Corregim blocs amb molta zona blanca, analitzem si el centroide esta en un extrem.
                                '''
                                if cY > ((self.recognizer_size_y / 2) + (self.recognizer_size_y*0.20)):
                                    ini_y += int(self.recognizer_size_y*0.25)
                                if cY < ((self.recognizer_size_y / 2) + (self.recognizer_size_y*0.20)):
                                    fi_y -= int(self.recognizer_size_y*0.25)
                                if cX < ((self.recognizer_size_x / 2) + (self.recognizer_size_x*0.20)):
                                    fi_x -= int(self.recognizer_size_y*0.25)
                                if cX > ((self.recognizer_size_y / 2) + (self.recognizer_size_y*0.20)):
                                    ini_x += int(self.recognizer_size_y*0.25)
                                
                            
                            except ZeroDivisionError:
                                print ('IdentifyWhites: ZeroDivisionError analitzant la zona ',zone)

                            if cX == 0 or cY == 0:
                                print ('El centroide surt 0,0 per tant no analitzem aquesta zona. Deu ser redundant')
                                
                            else:
                                '''
                                4.- Apliquem la segona fase del LMC: el calcul del moviment dels centroides (Motion).
                                -> VERSIO ITERATIVA:
                                '''
                                #self._MotionDetectionLMC(ini_x,ini_y,fi_x,fi_y,zone,cX,cY)

                                '''
                                -> VERSIO PARALELITZADA:
                                '''
                                th = Thread(target = self._run, args =(ini_x,ini_y,fi_x,fi_y,zone,cX,cY))
                                threads.append(th)
                                th.start()
                        except IndexError:
                            pass
            
            for th in threads:
                th.join() 
            threads = []
            
            self._analyseResultsLMC()
            
            if self.extinct:
                print ('FOC EXTINGIT')
                cv2.destroyAllWindows()
                break
    
    def _run(self,ini_x,ini_y,fi_x,fi_y,zone,cxx,cyy):
        
        rawCapture = PiRGBArray(self.picamera)
        num_frames = 2
        i = 0
        positives_warm = 0
        negatives_warm = 0
        positives = 0
        negatives = 0
        '''
        4.- En la fase del Motion, repetim el calcul dels centroides per als mateixos blocs (posicions de la imatge) que hem trobat
        anteriorment, pero en nous frames per observar-ne el moviment.
        '''
        for i in range(num_frames):
                    
            #cv2.imwrite('tmp'+str(zone)+str(i)+'.jpg',image.array)
            #image.truncate(0)
            #np_arr_rgb = cv2.imread('tmp'+str(zone)+str(i)+'.jpg',1)
            photo = hasel.rgb2hsl(self.actual_frame)
            photo[:,:,:] = photo[:,:,:] * 255
            black = np.uint8(photo[:,:,2])
                
            warm_results = []
            max_num_colors = fi_y*fi_x
            num = 0
            cX = 0
            cY = 0
            segment = black[ini_x:fi_x, ini_y:fi_y]
            
            M = cv2.moments(segment)
            
            try:
                cY = int(M["m10"] / M["m00"])
                cX = int(M["m01"] / M["m00"])

                if (abs(cX-cxx) > int(fi_x-ini_x) * self.motion_tolerance) :
                    positives += 1
                else:
                    negatives += 1
                    
                if (abs(cY-cyy) > int(fi_y-ini_y) * self.motion_tolerance):
                    positives += 1
                else:
                    negatives += 1
                '''
                5.- Executem la ultima part de l'algoritme, la deteccio de colors calids en els blocs.
                '''
                w_result = self._warmColorDetectionLMC(self.actual_frame,ini_x,ini_y,fi_x,fi_y,zone)
                if w_result:
                    positives_warm+=1
                else:
                    negatives_warm+=1

            except ZeroDivisionError:
                print ('isFire  -> ZeroDivisionError analitzant la zona: ',zone)
            i+=1

        self.results[zone] = ['Zona: '+str(zone),[positives,negatives],[positives_warm,negatives_warm],ini_x,ini_y,fi_x,fi_y]
        
    def _warmColorDetectionLMC(self,img_np_arr,ini_x,ini_y,fi_x,fi_y,zone):
        
        positives = 0
        negatives = 0
        max_num_colors = (fi_x-ini_x)*(fi_y-ini_y)
        
        segment = img_np_arr[ini_x:fi_x, ini_y:fi_y,:]
        freq = None
        
        p = 0
        n = 0
        for x in range(fi_x-ini_x):
            for y in range(fi_y-ini_y):
                R = segment[x,y,0]
                G = segment[x,y,1]
                B = segment[x,y,2]
                bgr = [B,G,R]
                if bgr in self.colors:
                    p+=1
                else:
                    n+=1

        if p >= max_num_colors * self.color_tolerance:
            return True
        else:
            return False

    def _getBetterZones(self):
        
        better_zones = [[] for i in range(len(self.results))]
        better_zones[0] = self.results[0]
        prev_x = self.results[0][3]
        prev_y = self.results[0][4]
        zones_diferent = 0

        for zone in self.results[1:]:
            if zone[4] == prev_y:
                #print 'La zona: ',zone
                better_zones[zones_diferent] = zone
                prev_y = zone[4]
            else:
                #print 'Zona diferent: ',zone
                zones_diferent+=1
                better_zones[zones_diferent].append(zone)
                prev_y = zone[4]
        
        #print ('Better zones: ',better_zones,'\n')

        noves_zones = [[] for i in range(zones_diferent)]
        tmp = 0
        for zones in better_zones[:zones_diferent]:
            if type(zones[0]) is list:
                for z in zones[0]:
                    noves_zones[tmp].append([z[1],z[2]])
            else:
                noves_zones[tmp] = zones[0]
            tmp+=1
                
        #print ('Result: ',noves_zones)

    def _searchFire(self):        
        
        self._moveMotors(0,1,0,1,80,80,1)
        time.sleep(0.5)
        self._moveMotors(0,1,0,1,80,self.potmotrn,1)
        time.sleep(0.5)
        self._moveMotors(0,0,0,0,0,0,1)

    def _analyseResultsLMC(self):
        
        positives_motion = 0
        positives_warm = 0
        negatives_motion = 0
        negatives_warm = 0
        positives = 0
        negatives = 0

        print ('Final results: ',self.results)
        if self.num_white_zones == 0:
            print ('NO VEIG FOC')
            #print ('No hi ha white zone')
        else:
            for result in self.results:
                if result != None:
                    if result[1][0]:
                        if result[1][1]:
                            res = result[1][0] / result[1][1]
                            if res >= 1:
                                positives+=1
                            else:
                                negatives+=1
                        else:
                            positives+=1
                    else:
                        negatives+=1
                    
                    if result[2][0]:
                        if result[2][1]:
                            res = result[2][0] / result[2][1]
                            if res >= 1:
                                positives+=1
                            else:
                                negatives+=1
                        else:
                            positives+=1
                    else:
                        negatives+=1

            if positives > negatives:
                print ('HIHA FOC')
                self._shootWater()
                
                PIN = 24
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(PIN,GPIO.OUT)
                GPIO.output(PIN,0)
               
            else:
                print ('NO HIHA FOC')
                
                PIN = 24
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(PIN,GPIO.OUT)
                GPIO.output(PIN,0)

                self._searchFire()
                
            time.sleep(1.5)
            self.results = []
        
    def map(x, in_min, in_max, out_min, out_max):
        return int((x-in_min) * (out_max-out_min) / (in_max-in_min) + out_min)

    def distance(self):
        #set GPIO Pins
        GPIO_TRIGGER = 6
        GPIO_ECHO = 5
         
        #set GPIO direction (IN / OUT)
        GPIO.setup(GPIO_TRIGGER, GPIO.OUT)
        GPIO.setup(GPIO_ECHO, GPIO.IN)
        # set Trigger to HIGH
        GPIO.output(GPIO_TRIGGER, True)
     
        # set Trigger after 0.01ms to LOW
        time.sleep(0.00001)
        GPIO.output(GPIO_TRIGGER, False)
     
        StartTime = time.time()
        StopTime = time.time()
     
        # save StartTime
        while GPIO.input(GPIO_ECHO) == 0:
            StartTime = time.time()
     
        # save time of arrival
        while GPIO.input(GPIO_ECHO) == 1:
            StopTime = time.time()
     
        # time difference between start and arrival
        TimeElapsed = StopTime - StartTime
        # multiply with the sonic speed (34300 cm/s)
        # and divide by 2, because there and back
        distance = (TimeElapsed * 34300) / 2

        mappedCy = map(distance,35,80,3.5,8.5)
    
        return distance,mappedCy

    def apuntadorFuego(self,dist):

        contours,hierarchy = cv2.findContours(self.actual_frame.copy(), 1, cv2.CHAIN_APPROX_NONE)
        fire=self.actual_frame.copy()

        #bif=0
        #sec=0
        # Find the biggest contour (if detected)
        #(640,480)
        mappedCx=0
        mappedCy=0
        if len(contours) > 0:
            c = max(contours, key=cv2.contourArea)
            cx = 0
            cy = 0
            try:
                M = cv2.moments(c)
                cx = int(M['m10']/(M['m00']))
                cy = int(M['m01']/(M['m00']))
            except ZeroDivisionError:
                print ('me cago en to')
            cv2.line(fire,(cx,0),(cx,480),(0,0,255),1)
            cv2.line(fire,(0,cy),(640,cy),(0,0,255),1)
            cv2.drawContours(fire, contours, -1, (0,255,0), 1)
            mappedCx = map(cx,0,640,-85,85)
            mappedCy = map(dist,0,300,3.5,8.5)
            
            print("Cx: "+str(cx)+" mappedCx: "+str(mappedCx))
            print("Cy: "+str(cy)+" mappedCy: "+str(mappedCy))
        else:
            print ("I don't see fire")
        return fire, mappedCx, mappedCy
    

        

if __name__ == '__main__':
    if len(sys.argv) > 1:
        f = FirefAIghter(int(sys.argv[1]),int(sys.argv[2]),float(sys.argv[3]),float(sys.argv[4]),float(sys.argv[5]))
    else:
        f = FirefAIghter()  
        
